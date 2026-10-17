from app.config import settings
from app.services import cache


def test_cache_uses_memory_when_redis_disabled(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_ENABLED", False)
    cache._client = None
    cache._redis_fail_until = 0
    cache._mem_cache.clear()

    assert cache._get_client() is None
    assert cache.set("adms:test:redis-disabled", {"ok": True}, ttl=30) is True
    assert cache.get("adms:test:redis-disabled") == {"ok": True}
