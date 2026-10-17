"""P2-B · Redis SETNX 分布式锁

用于多实例部署时防止 scheduler job 重复执行。

设计原则
--------
- **优雅降级**：Redis 不可用时 `try_acquire` 直接返回 True（等价于单实例部署，
  job 照常运行）。这保证了开发模式（无 Redis）和生产单实例下行为不变。
- **安全释放**：用 instance_id 作为 lock value + Lua check-and-delete，
  只能释放自己加的锁（避免 ttl 后被别人 acquire 然后被错误 release）。
- **TTL 强制**：锁必须有过期时间，进程崩溃也不会死锁。
- **异步友好**：scheduler 是 asyncio loop，本模块使用 `redis.asyncio` 客户端。

用法
----
    from app.services.distributed_lock import distributed_lock

    async def my_job():
        async with distributed_lock("scheduler:my_job", ttl=600) as acquired:
            if not acquired:
                logger.debug("[Scheduler] my_job held by another instance, skip")
                return
            await do_work()

行为表
------
| Redis 状态 | try_acquire 结果 | 说明 |
|---|---|---|
| 不可达 | `True`（degrade） | 假定单实例，job 正常执行 |
| 可达 + 锁空闲 | `True` | 抢到锁，过期前独占 |
| 可达 + 锁被他人占 | `False` | 让出执行权 |
"""
from __future__ import annotations

import functools
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from loguru import logger

from app.config import settings


# Lua: only DEL if value matches my instance_id (atomic check-and-delete)
_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# 进程级 instance_id（pod 重启或 reload 时变化）
# 形如 "hostname:pid:abcd1234" — 唯一可读
_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

# 缓存异步 Redis client + 失败冷却（与 cache.py 同样模式）
_async_client = None  # type: ignore[var-annotated]
_async_fail_until: float = 0.0
_RETRY_INTERVAL_SECS: float = 60.0


def get_instance_id() -> str:
    """暴露当前进程 instance_id，便于日志/调试."""
    return _INSTANCE_ID


def _reset_for_test() -> None:
    """测试钩子：重置内部缓存，用于隔离 test case 之间的状态."""
    global _async_client, _async_fail_until
    _async_client = None
    _async_fail_until = 0.0


async def _get_async_redis():
    """Lazy 初始化 redis.asyncio 客户端；失败后冷却 RETRY_INTERVAL_SECS 不重试."""
    global _async_client, _async_fail_until
    if _async_client is not None:
        return _async_client
    if time.time() < _async_fail_until:
        return None
    try:
        import redis.asyncio as aioredis  # noqa: WPS433 — lazy import
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _async_client = client
        logger.info(
            f"[DistLock] async Redis connected: {settings.REDIS_URL} "
            f"(instance={_INSTANCE_ID})"
        )
        return _async_client
    except Exception as e:  # noqa: BLE001 — graceful degrade on any failure
        _async_fail_until = time.time() + _RETRY_INTERVAL_SECS
        logger.info(
            f"[DistLock] Redis unavailable, single-instance mode "
            f"(retry in {_RETRY_INTERVAL_SECS:.0f}s): {e}"
        )
        return None


async def try_acquire(name: str, ttl: int) -> bool:
    """尝试抢占 `name` 锁，持有 `ttl` 秒。返回 True/False。

    Redis 不可达时返回 True（degrade）——保留与"无 Redis 单实例"等价行为。
    """
    client = await _get_async_redis()
    if client is None:
        return True
    try:
        # SET key value NX EX ttl → True if newly set, None if existed
        result = await client.set(name, _INSTANCE_ID, nx=True, ex=ttl)
        return bool(result)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[DistLock] try_acquire('{name}') Redis error → degrade: {e}")
        return True  # degrade


async def release(name: str) -> bool:
    """释放锁——仅释放自己加的（Lua 原子 compare-and-delete）。

    返回 True 表示真的删掉了；False 表示锁不存在或被他人持有（也可能 Redis 错）。
    """
    client = await _get_async_redis()
    if client is None:
        return True  # nothing to release in degrade mode
    try:
        result = await client.eval(_RELEASE_LUA, 1, name, _INSTANCE_ID)
        return bool(result)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[DistLock] release('{name}') Redis error: {e}")
        return False


@asynccontextmanager
async def distributed_lock(name: str, ttl: int = 600) -> AsyncIterator[bool]:
    """异步上下文管理器：包装 try_acquire / release 生命周期。

    - 进入时尝试抢锁，yield 出 acquired (True/False)
    - 离开时若 acquired==True 且 Redis 仍可用，自动释放
    - 异常路径也会 release（finally 保护）

    用法：
        async with distributed_lock("scheduler:lifecycle_check", ttl=600) as acquired:
            if not acquired:
                return
            ...
    """
    acquired = await try_acquire(name, ttl)
    try:
        yield acquired
    finally:
        if acquired:
            await release(name)


def with_distributed_lock(name: str, ttl: int = 600):
    """装饰器：把 async 函数包装成 distributed_lock 守卫的版本。

    锁竞争失败时函数不执行，返回 None。Redis 不可达时正常执行（degrade）。

    用法：
        @with_distributed_lock("scheduler:my_job", ttl=900)
        async def my_job():
            ...
    """
    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapped(*args, **kwargs):
            async with distributed_lock(name, ttl) as acquired:
                if not acquired:
                    logger.debug(
                        f"[DistLock] '{name}' held elsewhere, skip {func.__name__}"
                    )
                    return None
                return await func(*args, **kwargs)
        return wrapped
    return decorator
