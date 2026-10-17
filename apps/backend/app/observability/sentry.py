"""Sentry 初始化（P1-3，2026-04-24）

- 空 DSN 自动跳过 init（dev/CI 不上报）
- 集成 FastAPI + StarletteIntegration 自动捕获 HTTP 异常
- 集成 LoggingIntegration 自动把 logger.error 级日志作为 breadcrumb
- 集成 SqlAlchemyIntegration 自动追踪慢查询

调用方式：在 main.py 创建 FastAPI 实例**之前**调用 `init_sentry()`
（Sentry 要求早于路由挂载）。
"""

from __future__ import annotations

import logging as _py_logging
from loguru import logger


def init_sentry() -> bool:
    """从 settings 读取配置并 init Sentry。返回是否成功启用。"""
    from app.config import settings

    if not settings.SENTRY_DSN:
        logger.info("Sentry disabled (SENTRY_DSN empty)")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError as e:
        logger.warning(f"Sentry SDK not installed: {e}")
        return False

    environment = settings.SENTRY_ENVIRONMENT or settings.APP_ENV or "unknown"
    release = settings.SENTRY_RELEASE or None

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=environment,
        release=release,
        traces_sample_rate=float(settings.SENTRY_TRACES_SAMPLE_RATE or 0.0),
        # 默认 PII（IP、cookies、headers）不发送；生产合规必选
        send_default_pii=False,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            LoggingIntegration(
                level=_py_logging.INFO,        # INFO 以上作为 breadcrumb
                event_level=_py_logging.ERROR, # ERROR 以上作为 event 上报
            ),
            SqlalchemyIntegration(),
        ],
        # 忽略可预期的业务异常（后续可按需加 HTTPException 404/422）
        ignore_errors=[],
    )
    logger.info(
        f"Sentry enabled (env={environment}, release={release or 'auto'}, "
        f"traces_rate={settings.SENTRY_TRACES_SAMPLE_RATE})"
    )
    return True
