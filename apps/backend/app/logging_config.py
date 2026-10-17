"""结构化日志配置（P1-2，2026-04-24）

用 loguru 做统一 sink：
- stdout：dev 彩色可读 / prod 单行 JSON（通过 LOG_FORMAT 配置）
- 文件滚动：按天切分、自动 gzip 压缩、保留 LOG_RETENTION_DAYS 天

调用 `configure_logging()` 一次即可；多次调用会清空旧 sink 重配。

为与 Sentry / Prometheus 生态对齐，每条日志会携带：
- request_id    —— 由 RequestIDMiddleware 塞进 context
- asset / user  —— 业务上下文（可选，服务层自行 contextualize）

NOTE: 默认在 `main.py` startup 前调用，并在测试 conftest 中也要初始化（pytest 不走 startup）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from loguru import logger

_CONFIGURED = False


def configure_logging(
    log_format: str | None = None,
    log_level: str | None = None,
    log_dir: str | None = None,
    retention_days: int | None = None,
) -> None:
    """初始化全局 logger。幂等：多次调用会清空后重建 sink。

    Args:
        log_format: "json" 或 "pretty"，None 取 settings.LOG_FORMAT
        log_level: 级别名，None 取 settings.LOG_LEVEL
        log_dir: 文件 sink 目录，None 取 settings.LOG_DIR；空字符串关闭文件 sink
        retention_days: 文件保留天数，None 取 settings.LOG_RETENTION_DAYS
    """
    global _CONFIGURED

    # 运行时懒加载 settings，避免循环 import
    from app.config import settings

    fmt = (log_format or settings.LOG_FORMAT or "pretty").lower()
    level = (log_level or settings.LOG_LEVEL or "INFO").upper()
    dir_name = settings.LOG_DIR if log_dir is None else log_dir
    retention = retention_days if retention_days is not None else settings.LOG_RETENTION_DAYS

    # 清空已注册 sink（含 loguru 默认 stderr sink）
    logger.remove()

    # ── stdout sink ─────────────────────────────────────────
    logger.remove()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if fmt == "json":
        # 单行 JSON，便于 ELK / Loki / Datadog 采集
        # loguru.serialize=True 会输出 {"text": ..., "record": {...}}
        logger.add(
            sys.stdout,
            level=level,
            serialize=True,
            backtrace=False,   # 生产不打完整栈（由 Sentry 兜底）
            diagnose=False,    # 生产不打本地变量（安全考虑）
            enqueue=True,      # 多线程/多进程安全
        )
    else:
        # 人类可读带颜色；适合 dev
        logger.add(
            sys.stdout,
            level=level,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            colorize=True,
            backtrace=True,
            diagnose=True,
        )

    # ── 文件 sink（按日滚动 + gzip + 保留 N 天） ─────────────
    if dir_name:
        # 相对路径基于 cwd 即 backend 目录
        path = Path(dir_name)
        path.mkdir(parents=True, exist_ok=True)
        file_pattern = str(path / "app-{time:YYYY-MM-DD}.log")
        logger.add(
            file_pattern,
            level=level,
            rotation="00:00",                 # 每日午夜切
            retention=f"{retention} days",
            compression="gz",
            serialize=(fmt == "json"),        # 文件格式跟随 stdout
            enqueue=True,
            encoding="utf-8",
        )

    _CONFIGURED = True
    logger.info(
        "Logging configured: format={fmt} level={level} dir={dir} retention={days}d",
        fmt=fmt, level=level, dir=(dir_name or "<none>"), days=retention,
    )


def is_configured() -> bool:
    return _CONFIGURED
