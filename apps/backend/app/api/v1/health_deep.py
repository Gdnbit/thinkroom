"""深度健康检查（P1-1，2026-04-24）

提供 3 个端点（全部白名单，不受 APIKeyMiddleware 保护）：
- GET /health        —— 兼容旧版，=== /health/live
- GET /health/live   —— liveness probe：进程活即 200（不检查下游）
- GET /health/ready  —— readiness probe：检查 DB / Redis / MinIO / OpenRouter

readiness 返回字段：
{
  "status": "ready" | "degraded" | "unhealthy",
  "checks": {
    "database": {"ok": true, "latency_ms": 3.2, ...},
    "redis":    {"ok": false, "error": "connection refused", ...},
    ...
  },
  "timestamp": "2026-04-24T..."
}

状态判定：
- 所有检查通过 → 200 ready
- local_mvp: redis/minio 任一失败 → 200 degraded（可选下游，系统仍可响应）
- production_required: redis/minio 按生产配置进入强依赖，失败 → 503 unhealthy
- 数据库或已配置的 OpenRouter 失败 → 503 unhealthy
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import text

from app.config import build_deployment_profile, settings
from app.database import SessionLocal
from app.services.document_parser_adapter import get_document_parser_capabilities
from app.services.document_parser_identity import get_document_parser_identity


router = APIRouter()


async def _run_check(
    name: str,
    fn: Callable[[], Awaitable[Dict[str, Any]] | Dict[str, Any]],
    timeout: float = 3.0,
) -> Dict[str, Any]:
    """运行单个检查并计时；超时或异常都抓住不影响其他检查。"""
    t0 = time.perf_counter()
    try:
        if asyncio.iscoroutinefunction(fn):
            result = await asyncio.wait_for(fn(), timeout=timeout)
        else:
            # 同步检查下放到线程池，避免阻塞事件循环
            result = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {"ok": True, "latency_ms": latency_ms, **(result or {})}
    except asyncio.TimeoutError:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {"ok": False, "error": f"timeout>{timeout}s", "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "latency_ms": latency_ms}


# ── 具体检查 ────────────────────────────────────────────────


def _check_database() -> Dict[str, Any]:
    """SELECT 1 验证 DB 可达 + 轻量查询一张业务表。"""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        # 额外确认关键表可访问
        db.execute(text("SELECT COUNT(*) FROM claim_cards")).scalar()
        return {"dialect": db.bind.dialect.name}
    finally:
        db.close()


def _check_redis() -> Dict[str, Any]:
    """Redis PING；cache service 自动降级到内存字典，所以 redis 失败不致命。"""
    if not settings.REDIS_ENABLED:
        return {"ok": False, "configured": False, "error": "not configured"}
    try:
        from app.services.cache import _get_client
        client = _get_client()
        if client is None:
            return {"ok": False, "configured": True, "error": "unavailable"}
        pong = client.ping()
        return {"configured": True, "ping": bool(pong)}
    except ImportError:
        return {"ok": False, "configured": True, "error": "cache service unavailable"}


def _check_minio() -> Dict[str, Any]:
    """MinIO health/live 端点。"""
    endpoint = settings.MINIO_ENDPOINT
    if not endpoint:
        return {"ok": False, "error": "not configured"}
    # MinIO 健康端点 —— GET /minio/health/live
    scheme = "http"
    url = f"{scheme}://{endpoint}/minio/health/live"
    with httpx.Client(timeout=2.0) as client:
        r = client.get(url)
        r.raise_for_status()
    return {"endpoint": endpoint}


def _check_ingestion_worker() -> Dict[str, Any]:
    """Require a reachable durable ingestion worker only in production."""
    production = str(settings.APP_ENV or "").strip().lower() in {"production", "prod"}
    configured_mode = str(settings.INGESTION_DISPATCHER_MODE or "auto").strip().lower()
    mode = "celery" if configured_mode == "auto" and production else (
        "threadpool" if configured_mode == "auto" else configured_mode
    )
    if not production:
        return {
            "ok": True,
            "required": False,
            "mode": mode,
            "durable": mode == "celery",
            "worker_reachable": None,
            "worker_count": 0,
        }
    if mode != "celery":
        return {
            "ok": False,
            "required": True,
            "mode": mode,
            "durable": False,
            "worker_reachable": False,
            "worker_count": 0,
            "error": "durable dispatcher required",
        }
    from app.services.ingestion_dispatcher import get_celery_worker_status

    worker = get_celery_worker_status()
    return {
        "ok": worker.get("worker_reachable") is True,
        "required": True,
        "mode": "celery",
        "durable": True,
        "broker_configured": bool(worker.get("broker_configured")),
        "worker_reachable": worker.get("worker_reachable"),
        "worker_count": int(worker.get("worker_count") or 0),
        **({"probe_error_type": worker["probe_error_type"]} if worker.get("probe_error_type") else {}),
    }


async def _check_openrouter() -> Dict[str, Any]:
    """轻量 ping OpenRouter /api/v1/models 端点（无需 auth 即可返回公开列表头）。"""
    base = settings.OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1"
    # 只 HEAD 一下 /models，避免真的下载大 payload；部分代理不支持 HEAD 则 fallback GET 并丢弃 body
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            r = await client.head(f"{base.rstrip('/')}/models")
            if r.status_code >= 400:
                raise httpx.HTTPStatusError("head failed", request=r.request, response=r)
        except (httpx.HTTPStatusError, httpx.RequestError):
            # fallback GET
            r = await client.get(f"{base.rstrip('/')}/models")
            r.raise_for_status()
    return {"base_url": base}


# ── 路由 ────────────────────────────────────────────────────


@router.get("/health")
@router.get("/health/live")
async def health_live() -> JSONResponse:
    """Liveness：进程起来就返回 200。不检查下游。"""
    return JSONResponse(
        {
            "status": "ok",
            "version": "0.1.0",
            "env": settings.APP_ENV,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness：并行检查 DB / Redis / MinIO / OpenRouter。"""
    # 并行跑所有检查
    results = await asyncio.gather(
        _run_check("database", _check_database, timeout=3.0),
        _run_check("redis", _check_redis, timeout=2.0),
        _run_check("minio", _check_minio, timeout=2.0),
        _run_check("openrouter", _check_openrouter, timeout=3.5),
        _run_check("ingestion_worker", _check_ingestion_worker, timeout=2.0),
    )
    names = ["database", "redis", "minio", "openrouter", "ingestion_worker"]
    checks = dict(zip(names, results))

    # 决策：
    #   - database 是强依赖 → 失败即 unhealthy (503)
    #   - openrouter 是强依赖（LLM 核心）→ 失败即 unhealthy (503)，但测试/离线模式 OPENROUTER_API_KEY="" 时视为 degraded
    #   - redis / minio 是弱依赖 → 失败仅 degraded (200)
    has_or_key = bool(settings.OPENROUTER_API_KEY)
    parser_capabilities = get_document_parser_capabilities(settings.DOCUMENT_PARSER_ADAPTER)
    parser_ready = parser_capabilities["status"] == "ready"
    deployment_profile = build_deployment_profile(settings)
    required_checks = deployment_profile["required_checks"]
    blocking_checks: list[str] = []
    for check_name in required_checks:
        if check_name == "openrouter" and not has_or_key:
            continue
        check = checks.get(check_name)
        if not check or not check.get("ok"):
            blocking_checks.append(check_name)
    deployment_profile["blocking_checks"] = blocking_checks

    if blocking_checks:
        status, code = "unhealthy", 503
    elif not parser_ready:
        status, code = "degraded", 200
    elif not all(c["ok"] for c in checks.values()):
        status, code = "degraded", 200
    else:
        status, code = "ready", 200

    payload = {
        "status": status,
        "checks": checks,
        "capabilities": {
            "document_parser": parser_capabilities,
        },
        "identities": {
            "document_parser": get_document_parser_identity(),
        },
        "deployment_profile": deployment_profile,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if status != "ready":
        logger.warning("readiness={status}, checks={checks}", status=status, checks=checks)
    return JSONResponse(payload, status_code=code)
