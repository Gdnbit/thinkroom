"""Prometheus /metrics（P1-4，2026-04-24）

用 prometheus-fastapi-instrumentator 自动采集：
- http_requests_total{method,handler,status}       —— 请求计数
- http_request_duration_seconds{method,handler}    —— 响应延迟直方图
- http_request_size_bytes / response_size_bytes    —— 载荷大小

/metrics 暴露端点；默认不鉴权（与 Docker/K8s scrape 配合），
已在 APP_AUTH_WHITELIST 中放行。

生产建议：前端 ingress 限制只允许内网 IP 访问 /metrics。
"""

from __future__ import annotations

from fastapi import FastAPI
from loguru import logger


def _patch_instrumentator_route_matching() -> None:
    """Make prometheus-fastapi-instrumentator tolerate FastAPI 0.138 routers.

    FastAPI 0.138 adds ``_IncludedRouter`` placeholder routes to ``app.routes``.
    prometheus-fastapi-instrumentator 8.0.0 still assumes every matched route has
    a ``.path`` attribute, so every instrumented request can fail before reaching
    the real endpoint. The patch keeps the upstream matching behavior and adds
    support for these router containers by reading their include prefix and
    traversing ``original_router.routes``.
    """
    try:
        from prometheus_fastapi_instrumentator import routing
        from starlette.requests import HTTPConnection
        from starlette.routing import Match, Mount
        from starlette.types import Scope
    except Exception as e:
        logger.debug(f"instrumentator route matching patch skipped: {e}")
        return

    if getattr(routing.get_route_name, "_research_os_safe_patch", False):
        return

    def _join_route_path(prefix: str, path: str) -> str:
        if not prefix:
            return path or ""
        if not path or path == "/":
            return prefix
        return f"{prefix.rstrip('/')}/{path.lstrip('/')}"

    def _route_path(route) -> str:
        path = getattr(route, "path", None)
        if path is not None:
            return path
        include_context = getattr(route, "include_context", None)
        return getattr(include_context, "prefix", "") or ""

    def _route_children(route):
        children = getattr(route, "routes", None)
        if children:
            return children
        original_router = getattr(route, "original_router", None)
        return getattr(original_router, "routes", []) if original_router else []

    def _get_route_name(
        scope: Scope, routes: list, route_name: str | None = None
    ) -> str | None:
        for route in routes:
            matches = getattr(route, "matches", None)
            if matches is None:
                continue

            match, child_scope = matches(scope)
            path = _route_path(route)

            if match == Match.FULL:
                current_name = _join_route_path(route_name or "", path)
                children = _route_children(route)
                if children:
                    child_name = _get_route_name(
                        {**scope, **child_scope},
                        children,
                        current_name,
                    )
                    return child_name or current_name or None
                return current_name or None

            if match == Match.PARTIAL and route_name is None:
                route_name = path or None

        return None

    def get_route_name(request: HTTPConnection) -> str | None:
        app = request.app
        scope = request.scope
        routes = app.routes
        route_name = _get_route_name(scope, routes)

        if not route_name and app.router.redirect_slashes and scope["path"] != "/":
            redirect_scope = dict(scope)
            if scope["path"].endswith("/"):
                redirect_scope["path"] = scope["path"][:-1]
                trim = True
            else:
                redirect_scope["path"] = scope["path"] + "/"
                trim = False

            route_name = _get_route_name(redirect_scope, routes)
            if route_name is not None:
                route_name = route_name + "/" if trim else route_name[:-1]
        return route_name

    get_route_name._research_os_safe_patch = True  # type: ignore[attr-defined]
    routing._get_route_name = _get_route_name
    routing.get_route_name = get_route_name


def install_metrics(app: FastAPI) -> bool:
    """注册 instrumentator 并暴露 /metrics 端点。返回是否成功。"""
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
    except ImportError as e:
        logger.warning(f"prometheus_fastapi_instrumentator not available: {e}")
        return False

    _patch_instrumentator_route_matching()

    instrumentator = Instrumentator(
        should_group_status_codes=True,      # 2xx/3xx/4xx/5xx 分组（低基数）
        should_ignore_untemplated=True,      # 忽略 404 等未匹配路由的指标（避免 path 爆炸）
        should_respect_env_var=False,
        should_instrument_requests_inprogress=True,
        inprogress_name="http_requests_inprogress",
        inprogress_labels=True,
        excluded_handlers=["/health", "/health/live", "/health/ready", "/metrics"],
    )
    instrumentator.instrument(app)
    instrumentator.expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        should_gzip=True,
    )
    logger.info("Prometheus /metrics mounted")
    return True
