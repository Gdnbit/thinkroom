"""Redis 缓存服务

提供简单的 get/set/delete 操作。
- 连接失败时静默降级到进程内内存缓存
- 支持 JSON 序列化
- 支持 TTL 过期
"""
import json
import time
import hashlib
import threading
from typing import Any, Optional
from loguru import logger
from app.config import settings

_client = None
_redis_fail_until: float = 0  # 连接失败后冷却到此时间，避免每次请求都重试
_REDIS_RETRY_INTERVAL = 60.0  # Redis 连接失败后 60 秒内不再重试

# ── 进程内内存缓存（Redis 不可用时的降级方案）───────────────
_mem_cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expire_ts)
_mem_lock = threading.Lock()
_MEM_MAX_KEYS = 500  # 内存缓存最大条目数


def _mem_get(key: str) -> Optional[Any]:
    with _mem_lock:
        item = _mem_cache.get(key)
        if item is None:
            return None
        val, exp = item
        if time.time() > exp:
            _mem_cache.pop(key, None)
            return None
        return val


def _mem_set(key: str, value: Any, ttl: int) -> None:
    with _mem_lock:
        # 简单 LRU：超限时清除已过期的
        if len(_mem_cache) >= _MEM_MAX_KEYS:
            now = time.time()
            expired = [k for k, (_, exp) in _mem_cache.items() if now > exp]
            for k in expired:
                _mem_cache.pop(k, None)
            # 仍超限则删最早的 20%
            if len(_mem_cache) >= _MEM_MAX_KEYS:
                to_del = list(_mem_cache.keys())[:_MEM_MAX_KEYS // 5]
                for k in to_del:
                    _mem_cache.pop(k, None)
        _mem_cache[key] = (value, time.time() + ttl)


def _mem_delete(key: str) -> None:
    with _mem_lock:
        _mem_cache.pop(key, None)


def _get_client():
    """懒加载 Redis 客户端，失败时返回 None（静默降级到内存缓存）"""
    global _client, _redis_fail_until
    if not settings.REDIS_ENABLED:
        return None
    if _client is not None:
        return _client
    # 冷却期内不重试
    if time.time() < _redis_fail_until:
        return None
    try:
        import redis
        client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
            retry_on_timeout=False,
            health_check_interval=0,
        )
        client.ping()
        _client = client
        logger.info(f"[Cache] Redis connected: {settings.REDIS_URL}")
        return _client
    except Exception as e:
        _redis_fail_until = time.time() + _REDIS_RETRY_INTERVAL
        logger.info(f"[Cache] Redis unavailable, using in-memory cache (retry in {_REDIS_RETRY_INTERVAL:.0f}s): {e}")
        return None


def make_key(prefix: str, *args, **kwargs) -> str:
    """生成缓存 key：prefix + hash(args+kwargs)"""
    raw = f"{args}:{sorted(kwargs.items())}"
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"adms:{prefix}:{h}"


def get(key: str) -> Optional[Any]:
    """读取缓存，未命中或出错返回 None。Redis 不可用时降级到内存缓存。"""
    client = _get_client()
    if client is not None:
        try:
            val = client.get(key)
            if val is not None:
                return json.loads(val)
        except Exception:
            pass
    # Redis miss or unavailable → try in-memory
    return _mem_get(key)


def set(key: str, value: Any, ttl: int = 300) -> bool:
    """写入缓存，ttl 单位秒。Redis 不可用时降级到内存缓存。"""
    stored = False
    client = _get_client()
    if client is not None:
        try:
            client.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
            stored = True
        except Exception:
            pass
    # 始终同步写入内存缓存（作为兜底）
    try:
        _mem_set(key, value, ttl)
        stored = True
    except Exception:
        pass
    return stored


def delete(key: str) -> bool:
    """删除缓存"""
    deleted = False
    client = _get_client()
    if client is not None:
        try:
            client.delete(key)
            deleted = True
        except Exception:
            pass
    _mem_delete(key)
    return deleted or True


def cached(prefix: str, ttl: int = 300):
    """函数级缓存装饰器

    用法:
        @cached('market', ttl=300)
        def get_market_state(asset, timeframe='1d'):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            key = make_key(prefix, *args, **kwargs)
            hit = get(key)
            if hit is not None:
                logger.debug(f"[Cache] HIT {key}")
                return hit
            result = func(*args, **kwargs)
            if result is not None:
                set(key, result, ttl=ttl)
            return result
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator
