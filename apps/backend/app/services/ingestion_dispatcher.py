"""Bounded in-process dispatcher for async ingestion jobs.

FastAPI BackgroundTasks are tied to the request lifecycle. For file parsing and
LLM/OCR ingestion we need a small, explicit worker boundary so many uploads do
not create one ad-hoc request task per file.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import socket
import time
from typing import Any
import uuid

from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, safe_commit


def _database_dialect(database_url: str) -> str:
    scheme = str(database_url or "").strip().lower().split(":", 1)[0]
    return scheme.split("+", 1)[0] or "unknown"


def _effective_threadpool_worker_count(configured_workers: int, database_url: str) -> int:
    """SQLite is a single-writer store; serialize local ingestion transactions."""

    configured = max(1, int(configured_workers or 1))
    return 1 if _database_dialect(database_url) == "sqlite" else configured


_CONFIGURED_MAX_WORKERS = max(1, int(getattr(settings, "INGESTION_ASYNC_MAX_WORKERS", 2) or 1))
_DATABASE_DIALECT = _database_dialect(str(getattr(settings, "DATABASE_URL", "") or ""))
_MAX_WORKERS = _effective_threadpool_worker_count(
    _CONFIGURED_MAX_WORKERS,
    str(getattr(settings, "DATABASE_URL", "") or ""),
)
_MAX_OUTSTANDING = max(
    _MAX_WORKERS,
    int(getattr(settings, "INGESTION_ASYNC_MAX_OUTSTANDING", 16) or _MAX_WORKERS),
)
_RESERVATION_TTL_SECONDS = max(
    1,
    int(getattr(settings, "INGESTION_RESERVATION_TTL_SECONDS", 30) or 30),
)
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="ingestion-worker")
_lock = threading.Lock()
_inflight: dict[str, Future] = {}
_admitted_job_ids: set[str] = set()
_reservations: dict[str, dict[str, Any]] = {}


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.current_thread().name}"


def record_ingestion_job_heartbeat(
    db: Session,
    job_id: str,
    *,
    source_id: str | None = None,
    state: str = "running",
    worker_id: str | None = None,
    lease_seconds: int | None = None,
) -> dict[str, Any]:
    """Persist lightweight DB-queue lease metadata on the JobRun row."""
    from app.schemas.job_run import JobRun

    job = db.get(JobRun, job_id)
    if not job or job.job_type != "ingestion":
        return {"recorded": False, "job_id": job_id, "error": "ingestion job not found"}

    now = datetime.now(timezone.utc)
    lease_seconds = int(lease_seconds or getattr(settings, "INGESTION_JOB_LEASE_SECONDS", 600) or 600)
    lease_seconds = max(1, lease_seconds)
    heartbeat_at = now.isoformat()
    expires_at = (now + timedelta(seconds=lease_seconds)).isoformat()
    summary = dict(job.result_summary or {})
    metadata = {
        "lease_state": state,
        "lease_worker_id": worker_id or _worker_id(),
        "lease_heartbeat_at": heartbeat_at,
        "lease_expires_at": expires_at,
        "lease_seconds": lease_seconds,
    }
    if source_id:
        metadata["lease_source_id"] = source_id
    if not summary.get("lease_acquired_at"):
        metadata["lease_acquired_at"] = heartbeat_at
    summary.update(metadata)
    job.result_summary = summary
    job.updated_at = now
    safe_commit(db)
    return {"recorded": True, "job_id": job_id, **metadata}


def _record_ingestion_job_heartbeat_with_session(
    job_id: str,
    source_id: str,
    *,
    state: str,
) -> None:
    db = SessionLocal()
    try:
        record_ingestion_job_heartbeat(db, job_id, source_id=source_id, state=state)
    except Exception as exc:
        db.rollback()
        logger.debug(f"[IngestionDispatcher] heartbeat skipped job={job_id} source={source_id}: {exc}")
    finally:
        db.close()


def _mark_dispatch_failure(source_id: str, job_id: str, exc: Exception) -> None:
    """Persist a dispatcher-level failure that escaped the workflow."""
    from app.schemas.job_run import JobRun
    from app.schemas.raw_source import RawSource

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        error_type = exc.__class__.__name__
        job = db.get(JobRun, job_id)
        if job and job.status not in {"completed", "failed", "cancelled"}:
            job.status = "failed"
            job.completed_at = now
            job.error_message = f"Dispatcher failure ({error_type})"
            summary = dict(job.result_summary or {})
            summary.update(
                {
                    "stage": "dispatch_failed",
                    "pct": 100,
                    "message": "后台入库执行失败",
                    "dispatch_error_type": error_type,
                    "failed_at": now.isoformat(),
                }
            )
            job.result_summary = finalize_ingestion_lease_metadata(
                summary,
                state="failed",
                finalized_at=now,
            )
            job.updated_at = now

        source = db.get(RawSource, source_id)
        if source and source.status in {"pending", "processing"}:
            source.status = "failed"
            source.processing_error = f"Dispatch failed ({error_type})"
            source.updated_at = now
        safe_commit(db)
    except Exception as persist_exc:
        db.rollback()
        logger.warning(f"[IngestionDispatcher] failed to persist dispatch failure: {persist_exc}")
    finally:
        db.close()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def finalize_ingestion_lease_metadata(
    summary: dict[str, Any] | None,
    *,
    state: str,
    finalized_at: datetime | None = None,
) -> dict[str, Any]:
    """Close a persisted execution lease while retaining its audit identity."""

    result = dict(summary or {})
    if not any(key.startswith("lease_") for key in result):
        return result
    at = finalized_at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    result.update(
        {
            "lease_state": state,
            "lease_expires_at": None,
            "lease_released_at": at.isoformat(),
        }
    )
    return result


def claim_ingestion_job_execution(
    db: Session,
    job_id: str,
    source_id: str,
    *,
    worker_id: str,
    lease_seconds: int | None = None,
) -> dict[str, Any]:
    """Atomically claim one canonical JobRun before executing side effects."""
    from app.schemas.job_run import JobRun
    from app.schemas.raw_source import RawSource

    job = (
        db.query(JobRun)
        .filter(JobRun.id == job_id)
        .with_for_update()
        .first()
    )
    if not job or job.job_type != "ingestion":
        return {"execute": False, "reason": "job_not_found", "job_id": job_id, "source_id": source_id}
    if job.related_object_id and job.related_object_id != source_id:
        return {"execute": False, "reason": "source_mismatch", "job_id": job_id, "source_id": source_id}
    now = datetime.now(timezone.utc)
    if job.status == "completed":
        finalized_summary = finalize_ingestion_lease_metadata(
            job.result_summary,
            state="completed",
            finalized_at=job.completed_at or now,
        )
        if finalized_summary != dict(job.result_summary or {}):
            job.result_summary = finalized_summary
            job.updated_at = now
            safe_commit(db)
        return {
            "execute": False,
            "reason": "already_completed",
            "idempotent": True,
            "job_id": job_id,
            "source_id": source_id,
        }
    if job.status == "cancelled":
        finalized_summary = finalize_ingestion_lease_metadata(
            job.result_summary,
            state="cancelled",
            finalized_at=job.completed_at or now,
        )
        if finalized_summary != dict(job.result_summary or {}):
            job.result_summary = finalized_summary
            job.updated_at = now
            safe_commit(db)
        return {"execute": False, "reason": "cancelled", "job_id": job_id, "source_id": source_id}

    summary = dict(job.result_summary or {})
    source = db.get(RawSource, source_id)
    if source and source.status == "completed":
        summary.update(
            {
                "stage": "completed",
                "pct": 100,
                "message": "资料已完成处理，重复任务已安全收敛",
                "success": bool(summary.get("success", True)),
                "idempotent_recovery": True,
            }
        )
        summary = finalize_ingestion_lease_metadata(summary, state="completed", finalized_at=now)
        job.status = "completed"
        job.completed_at = job.completed_at or now
        job.result_summary = summary
        job.updated_at = now
        safe_commit(db)
        return {
            "execute": False,
            "reason": "already_completed_source",
            "idempotent": True,
            "job_id": job_id,
            "source_id": source_id,
        }
    lease_expires_at = _parse_iso_datetime(summary.get("lease_expires_at"))
    if job.status == "running" and lease_expires_at and lease_expires_at > now:
        retry_after_seconds = max(1, int((lease_expires_at - now).total_seconds()) + 1)
        return {
            "execute": False,
            "reason": "active_lease",
            "duplicate_active": True,
            "retryable": True,
            "retry_after_seconds": retry_after_seconds,
            "job_id": job_id,
            "source_id": source_id,
        }

    resolved_lease_seconds = max(
        1,
        int(lease_seconds or getattr(settings, "INGESTION_JOB_LEASE_SECONDS", 600) or 600),
    )
    expires_at = now + timedelta(seconds=resolved_lease_seconds)
    summary.update(
        {
            "lease_state": "running",
            "lease_worker_id": worker_id,
            "lease_heartbeat_at": now.isoformat(),
            "lease_expires_at": expires_at.isoformat(),
            "lease_seconds": resolved_lease_seconds,
            "lease_source_id": source_id,
            "lease_acquired_at": summary.get("lease_acquired_at") or now.isoformat(),
        }
    )
    job.status = "running"
    job.started_at = job.started_at or now
    job.related_object_id = source_id
    job.related_object_type = "raw_source"
    job.result_summary = summary
    job.updated_at = now
    safe_commit(db)
    return {
        "execute": True,
        "job_id": job_id,
        "source_id": source_id,
        "worker_id": worker_id,
        "lease_expires_at": expires_at.isoformat(),
    }


def run_ingestion_job(
    source_id: str,
    job_id: str,
    *,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Claim and execute an ingestion job from either dispatcher transport."""
    from app.workflows.ingestion_workflow import IngestionWorkflow

    resolved_worker_id = worker_id or _worker_id()
    lease_seconds = int(getattr(settings, "INGESTION_JOB_LEASE_SECONDS", 600) or 600)
    if resolved_worker_id.startswith("celery:"):
        lease_seconds = max(
            lease_seconds,
            int(getattr(settings, "CELERY_TASK_TIME_LIMIT_SECONDS", 3600) or 3600) + 300,
        )

    db = SessionLocal()
    try:
        claim = claim_ingestion_job_execution(
            db,
            job_id,
            source_id,
            worker_id=resolved_worker_id,
            lease_seconds=lease_seconds,
        )
        if not claim.get("execute"):
            logger.info(
                f"[IngestionDispatcher] execution skipped job={job_id} source={source_id} "
                f"reason={claim.get('reason')}"
            )
            return claim
        result = IngestionWorkflow(db).process_source(
            source_id,
            job_id=job_id,
            lease_owner_id=resolved_worker_id,
        )
        return {
            "executed": True,
            "success": bool(result.get("success")),
            "job_id": job_id,
            "source_id": source_id,
        }
    except Exception as exc:
        logger.error(
            f"[IngestionDispatcher] job failed job={job_id} source={source_id} "
            f"error_type={exc.__class__.__name__}"
        )
        _mark_dispatch_failure(source_id, job_id, exc)
        raise
    finally:
        db.close()


def _run_ingestion_job(source_id: str, job_id: str) -> None:
    run_ingestion_job(source_id, job_id)


def _resolved_mode() -> str:
    configured = str(getattr(settings, "INGESTION_DISPATCHER_MODE", "auto") or "auto").strip().lower()
    if configured == "auto":
        return "celery" if str(settings.APP_ENV).strip().lower() == "production" else "threadpool"
    return configured


def _cleanup_runtime_locked() -> None:
    for job_id, future in list(_inflight.items()):
        if future.done():
            _inflight.pop(job_id, None)
    now = time.monotonic()
    for reservation_id, reservation in list(_reservations.items()):
        if float(reservation.get("expires_at_monotonic") or 0.0) <= now:
            _reservations.pop(reservation_id, None)


def _runtime_status_locked() -> dict[str, Any]:
    _cleanup_runtime_locked()
    active_job_ids = sorted(set(_inflight) | set(_admitted_job_ids))
    reserved_count = sum(max(0, int(item.get("remaining") or 0)) for item in _reservations.values())
    available_capacity = max(0, _MAX_OUTSTANDING - len(active_job_ids) - reserved_count)
    mode = _resolved_mode()
    return {
        "dispatcher": "bounded_thread_pool" if mode == "threadpool" else mode,
        "mode": mode,
        "durable": mode == "celery",
        "max_workers": _MAX_WORKERS,
        "max_outstanding": _MAX_OUTSTANDING,
        "active_or_queued_count": len(active_job_ids),
        "reserved_count": reserved_count,
        "available_capacity": available_capacity,
        "saturated": available_capacity == 0,
        "job_ids": active_job_ids,
    }


def _receipt_fields_locked() -> dict[str, Any]:
    status = _runtime_status_locked()
    status.pop("job_ids", None)
    return status


def reserve_ingestion_capacity(count: int) -> dict[str, Any]:
    """Atomically reserve local queue slots for all-or-none batch admission."""
    requested_count = max(0, int(count or 0))
    with _lock:
        status = _runtime_status_locked()
        public_status = {key: value for key, value in status.items() if key != "job_ids"}
        if requested_count > status["available_capacity"]:
            return {
                "reserved": False,
                "reason": "capacity_exceeded",
                "retryable": True,
                "requested_count": requested_count,
                **public_status,
            }
        if requested_count == 0:
            return {
                "reserved": True,
                "reservation_id": None,
                "requested_count": 0,
                **public_status,
            }
        reservation_id = uuid.uuid4().hex
        _reservations[reservation_id] = {
            "remaining": requested_count,
            "expires_at_monotonic": time.monotonic() + _RESERVATION_TTL_SECONDS,
        }
        return {
            "reserved": True,
            "reservation_id": reservation_id,
            "requested_count": requested_count,
            **_receipt_fields_locked(),
        }


def release_ingestion_capacity(reservation_id: str | None) -> dict[str, Any]:
    with _lock:
        reservation = _reservations.pop(reservation_id, None) if reservation_id else None
        return {
            "released": reservation is not None,
            "released_count": max(0, int((reservation or {}).get("remaining") or 0)),
            **_receipt_fields_locked(),
        }


def _consume_reservation_locked(reservation_id: str) -> bool:
    _cleanup_runtime_locked()
    reservation = _reservations.get(reservation_id)
    if not reservation or int(reservation.get("remaining") or 0) <= 0:
        return False
    reservation["remaining"] = int(reservation["remaining"]) - 1
    if reservation["remaining"] <= 0:
        _reservations.pop(reservation_id, None)
    return True


def _cleanup_job(job_id: str, future: Future) -> None:
    with _lock:
        current = _inflight.get(job_id)
        if current is future:
            _inflight.pop(job_id, None)


def _submit_celery_job(source_id: str, job_id: str) -> dict[str, Any]:
    from app.tasks.ingestion import process_ingestion_job

    async_result = process_ingestion_job.apply_async(
        args=[source_id, job_id],
        task_id=job_id,
        queue=str(settings.CELERY_INGESTION_QUEUE or "ingestion"),
        serializer="json",
        ignore_result=True,
    )
    return {"task_id": str(async_result.id or job_id)}


def get_celery_worker_status() -> dict[str, Any]:
    """Probe worker reachability without exposing the configured broker URL."""
    if _resolved_mode() != "celery":
        return {
            "broker_configured": False,
            "worker_reachable": None,
            "worker_count": 0,
            "worker_names": [],
        }
    try:
        from app.celery_app import celery_app

        timeout = max(0.1, float(getattr(settings, "CELERY_INSPECT_TIMEOUT_SECONDS", 1.0) or 1.0))
        responses = celery_app.control.inspect(timeout=timeout).ping() or {}
        worker_names = sorted(str(name) for name in responses)
        return {
            "broker_configured": bool(settings.CELERY_BROKER_URL or settings.REDIS_URL),
            "worker_reachable": bool(worker_names),
            "worker_count": len(worker_names),
            "worker_names": worker_names,
        }
    except Exception as exc:
        return {
            "broker_configured": bool(settings.CELERY_BROKER_URL or settings.REDIS_URL),
            "worker_reachable": False,
            "worker_count": 0,
            "worker_names": [],
            "probe_error_type": exc.__class__.__name__,
        }


def submit_ingestion_job(
    source_id: str,
    job_id: str,
    *,
    reservation_id: str | None = None,
) -> dict[str, Any]:
    """Submit one job without allowing the process-local queue to grow unbounded."""
    with _lock:
        _cleanup_runtime_locked()
        existing = _inflight.get(job_id)
        if (existing and not existing.done()) or job_id in _admitted_job_ids:
            return {
                "submitted": False,
                "reason": "already_submitted",
                "idempotent": True,
                "source_id": source_id,
                "job_id": job_id,
                **_receipt_fields_locked(),
            }

        if reservation_id:
            if not _consume_reservation_locked(reservation_id):
                return {
                    "submitted": False,
                    "reason": "invalid_or_expired_reservation",
                    "retryable": True,
                    "source_id": source_id,
                    "job_id": job_id,
                    **_receipt_fields_locked(),
                }
        else:
            status = _runtime_status_locked()
            if status["available_capacity"] <= 0:
                return {
                    "submitted": False,
                    "reason": "capacity_exceeded",
                    "retryable": True,
                    "source_id": source_id,
                    "job_id": job_id,
                    **{key: value for key, value in status.items() if key != "job_ids"},
                }

        if settings.APP_ENV == "test" and not settings.INGESTION_ASYNC_RUN_IN_TESTS:
            return {
                "submitted": True,
                "source_id": source_id,
                "job_id": job_id,
                **_receipt_fields_locked(),
                "execution_skipped": "test_env",
            }
        mode = _resolved_mode()
        _admitted_job_ids.add(job_id)

    try:
        if mode == "celery":
            celery_receipt = _submit_celery_job(source_id, job_id)
            future = None
        elif mode == "threadpool":
            _record_ingestion_job_heartbeat_with_session(job_id, source_id, state="submitted")
            future = _executor.submit(_run_ingestion_job, source_id, job_id)
            celery_receipt = {}
        else:
            raise RuntimeError("unsupported ingestion dispatcher mode")
    except Exception as exc:
        with _lock:
            _admitted_job_ids.discard(job_id)
            receipt = _receipt_fields_locked()
        _mark_dispatch_failure(source_id, job_id, exc)
        logger.warning(
            f"[IngestionDispatcher] submit failed job={job_id} source={source_id} "
            f"error_type={exc.__class__.__name__}"
        )
        return {
            "submitted": False,
            "reason": "dispatch_failed",
            "retryable": True,
            "source_id": source_id,
            "job_id": job_id,
            "error_type": exc.__class__.__name__,
            **receipt,
        }

    with _lock:
        _admitted_job_ids.discard(job_id)
        if future is not None:
            _inflight[job_id] = future
        receipt = _receipt_fields_locked()

    if future is not None:
        future.add_done_callback(lambda fut: _cleanup_job(job_id, fut))
    logger.info(
        f"[IngestionDispatcher] submitted job={job_id} source={source_id} "
        f"active_or_queued={receipt['active_or_queued_count']} "
        f"max_outstanding={_MAX_OUTSTANDING} max_workers={_MAX_WORKERS}"
    )
    return {
        "submitted": True,
        "source_id": source_id,
        "job_id": job_id,
        **receipt,
        **celery_receipt,
    }


def list_inflight_ingestion_jobs() -> dict[str, Any]:
    """Return secret-safe dispatcher diagnostics for tests and operations."""
    with _lock:
        status = _runtime_status_locked()
    status.update(
        {
            "configured_max_workers": _CONFIGURED_MAX_WORKERS,
            "database_dialect": _DATABASE_DIALECT,
            "write_concurrency_policy": (
                "serialized_single_writer" if _DATABASE_DIALECT == "sqlite" else "configured_parallelism"
            ),
        }
    )
    if status["mode"] == "celery":
        status.update(get_celery_worker_status())
    return status
