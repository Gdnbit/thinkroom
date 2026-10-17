"""全局异常处理器（P1-3 配套，2026-04-24）

覆盖：
- HTTPException / StarletteHTTPException —— 保持原 status，但响应体加 request_id
- RequestValidationError —— 422，字段清单 + request_id
- 其他 Exception —— 500，生产环境不回包内部栈，request_id 塞 response 便于用户报障

所有异常都会被 loguru.exception() 记录，并通过 LoggingIntegration 自动上报 Sentry。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from loguru import logger

from app.utils.llm_client import LLMProviderUnavailableError


def _get_request_id(request: Request) -> str | None:
    # 优先从 scope.state（纯 ASGI 中间件写入）；回退 request.state（BaseHTTPMiddleware）
    scope_state = request.scope.get("state") or {}
    rid = scope_state.get("request_id") if isinstance(scope_state, dict) else None
    if rid:
        return rid
    return getattr(request.state, "request_id", None)


def _build_response(body: dict, status_code: int, request_id: str | None, headers: dict | None = None) -> JSONResponse:
    """JSONResponse + 统一注入 X-Request-Id 响应头。"""
    merged_headers = dict(headers or {})
    if request_id:
        merged_headers["X-Request-Id"] = request_id
    return JSONResponse(body, status_code=status_code, headers=merged_headers)


def install_exception_handler(app: FastAPI) -> None:
    @app.exception_handler(LLMProviderUnavailableError)
    async def _llm_provider_unavailable(request: Request, exc: LLMProviderUnavailableError):
        rid = _get_request_id(request)
        body = {
            "detail": "Model provider unavailable. Check OpenRouter configuration or retry later.",
            "status_code": 503,
            "error_code": "llm_provider_unavailable",
            "error": exc.error,
            "error_type": exc.error_type,
        }
        if rid:
            body["request_id"] = rid
        logger.error(
            "LLM provider unavailable on {method} {path}: {error} ({error_type})",
            method=request.method,
            path=request.url.path,
            error=exc.error,
            error_type=exc.error_type,
        )
        return _build_response(body, 503, rid)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        rid = _get_request_id(request)
        body = {
            "detail": exc.detail,
            "status_code": exc.status_code,
        }
        if rid:
            body["request_id"] = rid
        # 4xx 用 debug（减少噪音），5xx 用 error
        if exc.status_code >= 500:
            logger.error(
                "HTTPException {code} on {path}: {detail}",
                code=exc.status_code, path=request.url.path, detail=exc.detail,
            )
        else:
            logger.debug(
                "HTTPException {code} on {path}: {detail}",
                code=exc.status_code, path=request.url.path, detail=exc.detail,
            )
        return _build_response(body, exc.status_code, rid, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _val_exc(request: Request, exc: RequestValidationError):
        rid = _get_request_id(request)
        # pydantic v2 会把自定义 field_validator 抛出的 ValueError 实例
        # 直接放进 errors()[i]["ctx"]["error"]，那个对象不是 JSON serializable，
        # 会让 JSONResponse 渲染时炸。这里做一次"序列化清理"。
        safe_errors = []
        for err in exc.errors():
            err_copy = dict(err)
            ctx = err_copy.get("ctx")
            if isinstance(ctx, dict):
                err_copy["ctx"] = {k: (str(v) if isinstance(v, BaseException) else v) for k, v in ctx.items()}
            safe_errors.append(err_copy)

        body = {
            "detail": "Validation error",
            "errors": safe_errors,
        }
        if rid:
            body["request_id"] = rid
        logger.warning(
            "ValidationError on {path}: {errors}",
            path=request.url.path, errors=safe_errors,
        )
        return _build_response(body, 422, rid)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        rid = _get_request_id(request)
        # 完整栈记入日志（会被 Sentry 上报）
        logger.exception(
            "Unhandled exception on {method} {path}: {err}",
            method=request.method, path=request.url.path, err=str(exc),
        )
        body = {
            "detail": "Internal server error",
            "status_code": 500,
        }
        if rid:
            body["request_id"] = rid
        # dev 模式附带异常类名便于排障；生产不透露实现细节
        from app.config import settings
        if settings.APP_DEBUG:
            body["exception_type"] = type(exc).__name__
            body["exception_message"] = str(exc)[:500]
        return _build_response(body, 500, rid)
