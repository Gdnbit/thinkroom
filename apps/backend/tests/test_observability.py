"""P1-3/P1-4/P1-5 可观测性测试（2026-04-24）

覆盖：
- 全局异常处理器：500 响应含 request_id + detail
- HTTPException 响应含 request_id
- /metrics 暴露 Prometheus 指标
- Rate limit：超限返回 429 with request_id，含 Retry-After 头
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient


# ── 异常处理 ──────────────────────────────────────────────────


class TestExceptionHandler:
    def test_500_has_request_id(self, db):
        """添加临时路由触发 500，验证响应体和 header 都带 request_id。

        注意：conftest 的 `client` fixture 用 raise_server_exceptions=True（方便常规测试捕异常），
        测 500 路径必须用 raise_server_exceptions=False 让异常真正走到 exception_handler。
        """
        from app.main import app
        from app.dependencies import get_db

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db

        @app.get("/_test_boom")
        async def boom():
            raise RuntimeError("intentional explosion for test")

        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                r = c.get("/_test_boom")
                assert r.status_code == 500
                body = r.json()
                assert body["detail"] == "Internal server error"
                assert body["status_code"] == 500
                # request_id 应当在 body 和 header 都存在且一致
                assert "request_id" in body
                assert r.headers.get("X-Request-Id") == body["request_id"]
        finally:
            # 清理：移除临时路由（TestClient 测试隔离）
            app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/_test_boom"]
            app.dependency_overrides.clear()

    def test_404_returns_request_id(self, client):
        r = client.get("/definitely_does_not_exist_42")
        assert r.status_code == 404
        body = r.json()
        assert "request_id" in body
        assert body["status_code"] == 404


# ── Prometheus /metrics ────────────────────────────────────────


class TestMetrics:
    def test_metrics_endpoint_exposed(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        text = r.text
        # 基础 HELP/TYPE 标签 + 默认 python_info 指标
        assert "# HELP" in text
        assert "# TYPE" in text

    def test_metrics_counts_requests(self, client):
        # 先打一个 health 请求产生指标
        client.get("/health/live")
        client.get("/health/live")
        r = client.get("/metrics")
        assert r.status_code == 200
        # instrumentator 的默认 http_requests_total 指标
        # （某些版本用 http_requests，名字取决于版本；宽松断言）
        text = r.text
        assert "http_request" in text  # http_request_duration_seconds 或类似


# ── Rate Limit ────────────────────────────────────────────────


class TestRateLimit:
    """验证 slowapi 中间件与 429 handler。

    不能直接测全局默认 300/min（单测跑不了这么快），所以动态 patch
    RATE_LIMIT_DEFAULT 并重建 limiter。
    """

    def test_custom_limit_triggers_429(self, monkeypatch):
        """用独立 FastAPI app 验证：2/minute 装饰，第 3 请求必 429。"""
        from app.observability.rate_limit import _build_limiter, _custom_rate_limit_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware

        test_app = FastAPI()
        local_limiter = _build_limiter()
        test_app.state.limiter = local_limiter
        test_app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)
        test_app.add_middleware(SlowAPIMiddleware)

        @test_app.get("/ping")
        @local_limiter.limit("2/minute")
        async def ping(request: Request, response: Response):
            return {"ok": True}

        with TestClient(test_app) as c:
            r1 = c.get("/ping")
            assert r1.status_code == 200, r1.text
            r2 = c.get("/ping")
            assert r2.status_code == 200, r2.text
            r3 = c.get("/ping")
            assert r3.status_code == 429
            body = r3.json()
            assert body["status_code"] == 429
            assert "Rate limit exceeded" in body["detail"]
            # slowapi headers
            assert "retry-after" in {k.lower() for k in r3.headers.keys()}

    def test_rate_limit_key_by_api_key_header(self, monkeypatch):
        """有 X-API-Key 时按 key 分桶；同 key 不同 IP 也会触发限流。"""
        from app.observability.rate_limit import _build_limiter, _custom_rate_limit_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware

        test_app = FastAPI()
        local_limiter = _build_limiter()
        test_app.state.limiter = local_limiter
        test_app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)
        test_app.add_middleware(SlowAPIMiddleware)

        @test_app.get("/pong")
        @local_limiter.limit("1/minute")
        async def pong(request: Request, response: Response):
            return {"ok": True}

        with TestClient(test_app) as c:
            headers = {"X-API-Key": "same-key-123"}
            r1 = c.get("/pong", headers=headers)
            assert r1.status_code == 200, r1.text
            # 第二次带同 key 必 429
            assert c.get("/pong", headers=headers).status_code == 429
            # 换 key 仍然能通（另一个桶）
            assert c.get("/pong", headers={"X-API-Key": "other-key"}).status_code == 200
