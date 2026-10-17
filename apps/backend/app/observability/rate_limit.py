"""限流（P1-5，2026-04-24）

用 slowapi 实现基于 IP + 可选 API key 的 token bucket：
- 全局默认：settings.RATE_LIMIT_DEFAULT（如 "300/minute"）
- 关键端点用 `@limiter.limit(settings.RATE_LIMIT_CHAT)` 等装饰器覆盖

使用方法：
  from app.observability.rate_limit import limiter
  @router.post("/chat")
  @limiter.limit(settings.RATE_LIMIT_CHAT)
  async def chat(request: Request, ...):
      ...

关键：被装饰的路由函数**必须**有 `request: Request` 参数（slowapi 要求）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from loguru import logger
from app.config import settings


def _key_func(request: Request) -> str:
    """
    限流 key：优先按 X-API-Key 分桶（同一 key 全局限流，不被多 IP 绕过）；
    否则回退到 IP 地址。
    """
    api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if api_key:
        # 截短避免 key 过长，前 16 位足够区分
        return f"key:{api_key[:16]}"
    return f"ip:{get_remote_address(request)}"


def _build_limiter() -> Limiter:
    """构造 Limiter。

    设计选择：
    - **不设 default_limits** —— slowapi 全局默认会强制包装所有 handler，要求 `response: Response` 参数。
      我们要求关键端点显式 `@limiter.limit(settings.RATE_LIMIT_XXX)` 并声明 response 参数，
      避免在非关键路径（/health、/metrics、小查询）上引入非必要的依赖约束。
    - headers_enabled=True 让响应自动带 X-RateLimit-* 头
    - 开发默认内存存储；生产需配置 settings.RATE_LIMIT_STORAGE_URI 为 Redis 等共享存储
    """
    return Limiter(
        key_func=_key_func,
        default_limits=[],  # 仅显式装饰的端点限流
        headers_enabled=True,
        storage_uri=settings.RATE_LIMIT_STORAGE_URI,
    )


# 全局单例
limiter: Limiter = _build_limiter()


def get_limiter() -> Limiter:
    return limiter


async def _custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """429 响应附带 request_id（如有）、X-RateLimit-* 头与 Retry-After 便于客户端退避。"""
    rid = getattr(request.state, "request_id", None)
    body = {
        "detail": f"Rate limit exceeded: {exc.detail}",
        "status_code": 429,
    }
    if rid:
        body["request_id"] = rid
    logger.warning(
        "Rate limit exceeded on {path}: {detail} (key={key})",
        path=request.url.path, detail=exc.detail, key=_key_func(request),
    )
    response = JSONResponse(body, status_code=429)
    # 注入 slowapi 自带的 X-RateLimit-Limit / Remaining / Reset + Retry-After
    try:
        view_rate_limit = getattr(request.state, "view_rate_limit", None)
        if view_rate_limit is not None:
            request.app.state.limiter._inject_headers(response, view_rate_limit)
    except Exception as e:
        logger.debug(f"failed to inject rate-limit headers: {e}")
    return response


def install_rate_limit(app: FastAPI) -> None:
    """把 limiter 挂到 app 上并注册 429 handler。"""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)
    logger.info("SlowAPI rate limiter installed")
