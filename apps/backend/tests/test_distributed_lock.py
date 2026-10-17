"""P2-B 单元测试 · Redis 分布式锁 + Scheduler 配置开关

不依赖外部 Redis：用 monkeypatched async client 模拟 SETNX 语义，
验证锁竞争 / 优雅降级 / scheduler 启停条件。
"""
from __future__ import annotations

import asyncio
import pytest

from app.services import distributed_lock as dl


# ── 极简内存 Redis mock —— 只实现 set(NX,EX) / eval(LUA) / ping ──
class _FakeAsyncRedis:
    """模拟 redis.asyncio.Redis 用于隔离测试.

    支持：
    - `set(name, value, nx=True, ex=ttl)`：NX 语义，已存在返回 None，否则 True
    - `eval(lua, 1, key, arg)`：仅识别 RELEASE_LUA pattern（compare-and-delete）
    - `ping()`：always 成功
    """

    def __init__(self):
        self._store: dict[str, str] = {}

    async def ping(self):
        return True

    async def set(self, name, value, nx=False, ex=None):
        if nx and name in self._store:
            return None  # already held
        self._store[name] = value
        return True

    async def eval(self, lua, numkeys, *args):
        # 我们只期望 _RELEASE_LUA：keys=[name], argv=[instance_id]
        # 第一参数是 key，第二参数是 expected value
        key = args[0]
        expected = args[1]
        if self._store.get(key) == expected:
            del self._store[key]
            return 1
        return 0


@pytest.fixture(autouse=True)
def _reset_lock_state():
    """每个测试前重置内部 client 缓存 + 失败冷却."""
    dl._reset_for_test()
    yield
    dl._reset_for_test()


# ── try_acquire / release ────────────────────────────────────────


@pytest.mark.asyncio
async def test_try_acquire_returns_true_when_redis_unavailable(monkeypatch):
    """Redis 不可达 → degrade 路径 → True."""
    async def _no_redis():
        return None
    monkeypatch.setattr(dl, "_get_async_redis", _no_redis)

    assert await dl.try_acquire("test:no_redis", ttl=10) is True


@pytest.mark.asyncio
async def test_try_acquire_releases_lock_first_then_collision(monkeypatch):
    """有 Redis 时：第一次成功，第二次（同名）失败；释放后再抢成功."""
    fake = _FakeAsyncRedis()

    async def _fake():
        return fake
    monkeypatch.setattr(dl, "_get_async_redis", _fake)

    # First acquire wins
    assert await dl.try_acquire("test:concurrent", ttl=60) is True
    # Second acquire (same instance, same name) collides
    assert await dl.try_acquire("test:concurrent", ttl=60) is False
    # Release returns True (owner)
    assert await dl.release("test:concurrent") is True
    # After release, can re-acquire
    assert await dl.try_acquire("test:concurrent", ttl=60) is True


@pytest.mark.asyncio
async def test_release_only_succeeds_for_owner(monkeypatch):
    """compare-and-delete: 不是自己的锁释放无效."""
    fake = _FakeAsyncRedis()

    async def _fake():
        return fake
    monkeypatch.setattr(dl, "_get_async_redis", _fake)

    assert await dl.try_acquire("test:owner", ttl=60) is True

    # 模拟另一实例的 ID（直接改 fake store 里的 value）
    fake._store["test:owner"] = "other-instance:9999:zzzzzzzz"

    # 现在 release 应该返回 False（不是我加的锁）
    assert await dl.release("test:owner") is False
    # 锁还在
    assert "test:owner" in fake._store


# ── distributed_lock 上下文管理器 ──────────────────────────────────


@pytest.mark.asyncio
async def test_distributed_lock_context_manager_releases(monkeypatch):
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(dl, "_get_async_redis", lambda: _async_passthrough(fake))

    async with dl.distributed_lock("test:ctx", ttl=60) as ok:
        assert ok is True
        assert "test:ctx" in fake._store

    # 退出后自动释放
    assert "test:ctx" not in fake._store


@pytest.mark.asyncio
async def test_distributed_lock_releases_on_exception(monkeypatch):
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(dl, "_get_async_redis", lambda: _async_passthrough(fake))

    with pytest.raises(RuntimeError):
        async with dl.distributed_lock("test:exc", ttl=60) as ok:
            assert ok is True
            raise RuntimeError("boom")

    # finally 路径也释放了
    assert "test:exc" not in fake._store


@pytest.mark.asyncio
async def test_distributed_lock_yields_false_when_held(monkeypatch):
    """当锁被他人占时，yield False，且不调用 release."""
    fake = _FakeAsyncRedis()
    fake._store["test:held"] = "someone-else"
    monkeypatch.setattr(dl, "_get_async_redis", lambda: _async_passthrough(fake))

    async with dl.distributed_lock("test:held", ttl=60) as ok:
        assert ok is False

    # 不能误删别人的锁
    assert fake._store["test:held"] == "someone-else"


# ── @with_distributed_lock 装饰器 ──────────────────────────────────


@pytest.mark.asyncio
async def test_with_distributed_lock_runs_when_acquired(monkeypatch):
    fake = _FakeAsyncRedis()
    monkeypatch.setattr(dl, "_get_async_redis", lambda: _async_passthrough(fake))

    counter = {"n": 0}

    @dl.with_distributed_lock("test:dec_run", ttl=60)
    async def my_job():
        counter["n"] += 1
        return "done"

    result = await my_job()
    assert result == "done"
    assert counter["n"] == 1
    # 退出后释放
    assert "test:dec_run" not in fake._store


@pytest.mark.asyncio
async def test_with_distributed_lock_skips_when_held(monkeypatch):
    fake = _FakeAsyncRedis()
    fake._store["test:dec_skip"] = "other"
    monkeypatch.setattr(dl, "_get_async_redis", lambda: _async_passthrough(fake))

    counter = {"n": 0}

    @dl.with_distributed_lock("test:dec_skip", ttl=60)
    async def my_job():
        counter["n"] += 1
        return "should-not-run"

    result = await my_job()
    assert result is None
    assert counter["n"] == 0
    # 别人的锁不动
    assert fake._store["test:dec_skip"] == "other"


@pytest.mark.asyncio
async def test_with_distributed_lock_runs_on_redis_unavailable(monkeypatch):
    """优雅降级：Redis 不可达时函数照常跑."""
    async def _no_redis():
        return None
    monkeypatch.setattr(dl, "_get_async_redis", _no_redis)

    @dl.with_distributed_lock("test:dec_degrade", ttl=60)
    async def my_job():
        return 42

    assert await my_job() == 42


# ── Scheduler 启动开关 ────────────────────────────────────────────


def test_scheduler_skips_when_app_env_test(monkeypatch):
    """APP_ENV=test 时 start_scheduler 直接跳过."""
    from app.services import scheduler as sch
    from app.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "APP_SCHEDULER_ENABLED", True)

    sch._scheduler_task = None
    sch.start_scheduler()
    assert sch._scheduler_task is None  # 没启动


def test_scheduler_skips_when_disabled_flag(monkeypatch):
    """APP_SCHEDULER_ENABLED=false 时跳过."""
    from app.services import scheduler as sch
    from app.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "APP_SCHEDULER_ENABLED", False)

    sch._scheduler_task = None
    sch.start_scheduler()
    assert sch._scheduler_task is None


# ── 帮助函数 ───────────────────────────────────────────────────────


def _async_passthrough(value):
    """Return an async coroutine that yields `value` (for monkeypatching `_get_async_redis`)."""
    async def _coro():
        return value
    return _coro()
