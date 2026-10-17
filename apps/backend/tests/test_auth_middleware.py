"""APIKeyMiddleware 单元测试（P0-1，2026-04-22）。

验证 4 个行为:
  1. `APP_API_KEY=""` 默认完全放行（当前所有测试环境）
  2. `APP_API_KEY` 设置后，无/错 header 返回 401
  3. 正确 header 通过
  4. 白名单路径（/health、/docs）不受影响
  5. CORS preflight OPTIONS 不校验
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.auth import APIKeyMiddleware


def _make_app(api_key: str = "", whitelist=None):
    """构造一个最小 FastAPI app 并挂 APIKeyMiddleware，独立于全局 settings。"""
    from app.config import settings as _settings

    _orig_key = _settings.APP_API_KEY
    _orig_wl = list(_settings.APP_AUTH_WHITELIST)
    _settings.APP_API_KEY = api_key
    if whitelist is not None:
        _settings.APP_AUTH_WHITELIST = list(whitelist)

    app = FastAPI()
    app.add_middleware(APIKeyMiddleware)

    @app.get("/api/v1/protected")
    async def protected():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # 恢复原 settings 的 teardown 通过闭包返回
    def _restore():
        _settings.APP_API_KEY = _orig_key
        _settings.APP_AUTH_WHITELIST = _orig_wl

    return app, _restore


class TestAPIKeyMiddlewareDisabled:
    """APP_API_KEY='' 时：完全放行，保持 dev/test 兼容。"""

    def test_no_key_configured_allows_all(self):
        app, restore = _make_app(api_key="")
        try:
            with TestClient(app) as c:
                r = c.get("/api/v1/protected")
                assert r.status_code == 200
                assert r.json() == {"ok": True}
                r = c.get("/health")
                assert r.status_code == 200
        finally:
            restore()


class TestAPIKeyMiddlewareEnabled:
    """APP_API_KEY 非空时的完整鉴权矩阵。"""

    def test_missing_header_returns_401(self):
        app, restore = _make_app(api_key="secret-token-xyz")
        try:
            with TestClient(app) as c:
                r = c.get("/api/v1/protected")
                assert r.status_code == 401
                body = r.json()
                assert body["type"] == "auth_error"
        finally:
            restore()

    def test_wrong_header_returns_401(self):
        app, restore = _make_app(api_key="secret-token-xyz")
        try:
            with TestClient(app) as c:
                r = c.get("/api/v1/protected", headers={"X-API-Key": "wrong"})
                assert r.status_code == 401
        finally:
            restore()

    def test_correct_header_passes(self):
        app, restore = _make_app(api_key="secret-token-xyz")
        try:
            with TestClient(app) as c:
                r = c.get("/api/v1/protected", headers={"X-API-Key": "secret-token-xyz"})
                assert r.status_code == 200
                assert r.json() == {"ok": True}
        finally:
            restore()

    def test_lowercase_header_also_works(self):
        """HTTP headers 大小写不敏感，`x-api-key` 也应通过。"""
        app, restore = _make_app(api_key="secret-token-xyz")
        try:
            with TestClient(app) as c:
                r = c.get("/api/v1/protected", headers={"x-api-key": "secret-token-xyz"})
                assert r.status_code == 200
        finally:
            restore()

    def test_whitelist_path_bypasses_auth(self):
        """/health 等白名单即使没 header 也放行。"""
        app, restore = _make_app(api_key="secret-token-xyz", whitelist=["/health", "/docs"])
        try:
            with TestClient(app) as c:
                r = c.get("/health")
                assert r.status_code == 200
        finally:
            restore()

    def test_options_preflight_bypasses_auth(self):
        """CORS OPTIONS 预检不带 X-API-Key 也必须放行。"""
        app, restore = _make_app(api_key="secret-token-xyz")
        try:
            with TestClient(app) as c:
                # Starlette's TestClient 对 OPTIONS 的处理：走中间件链
                r = c.options("/api/v1/protected", headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                })
                # 中间件放行后交给路由；FastAPI 默认对未定义 OPTIONS 返回 405
                # 关键是不能被认证中间件拦成 401
                assert r.status_code != 401
        finally:
            restore()


class TestConstantTimeCompare:
    """旁路时序攻击防护:长度不等/逐字符异或累积。"""

    def test_length_mismatch_false(self):
        assert APIKeyMiddleware._constant_time_eq("abc", "abcd") is False

    def test_equal_strings(self):
        assert APIKeyMiddleware._constant_time_eq("abc", "abc") is True

    def test_different_strings_same_length(self):
        assert APIKeyMiddleware._constant_time_eq("abc", "xyz") is False
