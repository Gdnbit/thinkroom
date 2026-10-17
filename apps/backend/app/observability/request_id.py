"""Request ID 中间件（P1-3 配套，2026-04-24）

为每个请求生成（或透传 X-Request-Id header 的）UUID，塞进：
- scope["state"]["request_id"] / request.state.request_id —— 路由可读
- response header X-Request-Id —— 客户端可追踪（异常路径也能注入）
- loguru contextualize —— 同请求内所有日志都带 request_id
- Sentry tag —— Sentry event 可按 request_id 关联

实现注意：
- 用**纯 ASGI 中间件**而非 BaseHTTPMiddleware，因为后者在异常路径上不会
  回流到 dispatch 的后置代码，header 无法注入到 exception handler 的响应上
- 通过 send wrapper 拦截 http.response.start 消息注 header

调用：`install_request_id(app)` 在所有其他中间件之前
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send, Message
from loguru import logger


HEADER_NAME = "X-Request-Id"
_HEADER_NAME_BYTES = HEADER_NAME.lower().encode("ascii")


class RequestIDMiddleware:
    """纯 ASGI 中间件。兼容异常路径的 response header 注入。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 客户端带来的 request id 优先（便于跨服务追踪）；否则新生成
        headers = dict(scope.get("headers") or [])
        incoming = headers.get(_HEADER_NAME_BYTES) or headers.get(HEADER_NAME.encode("ascii"))
        incoming_str = incoming.decode("latin-1") if incoming else ""
        request_id = incoming_str if incoming_str and len(incoming_str) <= 128 else uuid.uuid4().hex

        # 塞进 scope.state 以便 request.state.request_id 可读
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        # send wrapper：在每次 response 开始前注入 X-Request-Id 头
        rid_bytes = request_id.encode("ascii")

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers_list = list(message.get("headers") or [])
                # 避免重复：去除已有的 X-Request-Id
                headers_list = [(k, v) for (k, v) in headers_list if k.lower() != _HEADER_NAME_BYTES]
                headers_list.append((_HEADER_NAME_BYTES, rid_bytes))
                message["headers"] = headers_list
            await send(message)

        # loguru 上下文：本请求内所有 log 都会带 request_id 字段
        with logger.contextualize(request_id=request_id):
            # Sentry tag（如果启用）
            try:
                import sentry_sdk
                with sentry_sdk.configure_scope() as scope_sentry:
                    scope_sentry.set_tag("request_id", request_id)
            except ImportError:
                pass
            except Exception:
                pass

            await self.app(scope, receive, _send)


def install_request_id(app: FastAPI) -> None:
    app.add_middleware(RequestIDMiddleware)
