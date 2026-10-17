"""API Key 认证中间件（P0-1，2026-04-22 新增）。

设计原则:
- **默认行为向后兼容**:`settings.APP_API_KEY == ""` 时完全放行（dev/test/目前线上所有环境都不会 breaking）。
- **生产启用**:`.env.production` 设置 `APP_API_KEY=<32字节 token>`，所有 `/api/v1/**` 必须带 `X-API-Key: <value>` 请求头。
- **白名单**:`APP_AUTH_WHITELIST` 里的 path 前缀无需鉴权（`/health`、`/docs`、`/redoc`、`/openapi.json`、`/metrics`）。
- **CORS preflight**:`OPTIONS` 方法一律放行（浏览器 preflight 不带 `X-API-Key`）。

使用方式:在 `main.py` 注册 `APIKeyMiddleware`。
"""
from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import settings


class APIKeyMiddleware(BaseHTTPMiddleware):
    """简单 header-based API Key 校验。

    - 若 `settings.APP_API_KEY` 为空 → 完全跳过（dev 默认）。
    - 若请求 path 命中 `APP_AUTH_WHITELIST` 前缀 → 跳过。
    - 若方法是 OPTIONS（CORS preflight） → 跳过。
    - 否则校验 `X-API-Key` 头与 `APP_API_KEY` 精确匹配（常量时间比较避免旁路）。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._expected_key = settings.APP_API_KEY or ""
        self._whitelist = tuple(settings.APP_AUTH_WHITELIST or [])
        self._enabled = bool(self._expected_key)

    def _is_whitelisted(self, path: str) -> bool:
        return any(path == wl or path.startswith(wl + "/") or path.startswith(wl) for wl in self._whitelist)

    @staticmethod
    def _constant_time_eq(a: str, b: str) -> bool:
        """常量时间比较，避免按字节短路的时序攻击。"""
        if len(a) != len(b):
            return False
        result = 0
        for x, y in zip(a, b):
            result |= ord(x) ^ ord(y)
        return result == 0

    async def dispatch(self, request: Request, call_next):
        if not self._enabled:
            return await call_next(request)

        if request.method == "OPTIONS":
            return await call_next(request)

        if self._is_whitelisted(request.url.path):
            return await call_next(request)

        provided = request.headers.get("x-api-key") or request.headers.get("X-API-Key") or ""
        if not provided or not self._constant_time_eq(provided, self._expected_key):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "detail": "Invalid or missing API key. Provide `X-API-Key` header.",
                    "type": "auth_error",
                },
            )

        return await call_next(request)
