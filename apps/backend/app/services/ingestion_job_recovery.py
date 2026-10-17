"""Recovery helpers for ingestion jobs left pending/running across restarts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.database import safe_commit
from app.schemas.job_run import JobRun
from app.schemas.raw_source import RawSource
from app.services.ingestion_dispatcher import submit_ingestion_job
from app.services.progress_tracker import progress_tracker


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(started_at: datetime | None, completed_at: datetime) -> int | None:
    if not started_at:
        return None
    try:
        return int((completed_at - started_at).total_seconds() * 1000)
    except Exception:
        return None


def _merge_summary(summary: dict | None, **updates: Any) -> dict:
    data = dict(summary or {})
    data.update(updates)
    return data


def _resolve_max_retries(max_retries: int | None) -> int:
    if max_retries is None:
        max_retries = getattr(settings, "INGESTION_RECOVERY_MAX_RETRIES", 3)
    try:
        return max(0, int(max_retries))
    except Exception:
        return 3


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


def _lease_expired(summary: dict, now: datetime) -> bool:
    expires_at = _parse_iso_datetime(summary.get("lease_expires_at"))
    return bool(expires_at and expires_at <= now)


def cancel_ingestion_job(
    db: Session,
    job_id: str,
    *,
    reason: str = "用户取消",
) -> dict[str, Any]:
    """Cancel a pending/running ingestion job and mark its source as failed."""
    job = db.get(JobRun, job_id)
    if not job or job.job_type != "ingestion":
        return {"success": False, "status_code": 404, "error": "ingestion job not found"}

    if job.status == "cancelled":
        return {
            "success": True,
            "job_id": job.id,
            "status": "cancelled",
            "source_id": job.related_object_id,
            "already_cancelled": True,
        }
    if job.status in {"completed", "failed"}:
        return {
            "success": False,
            "status_code": 400,
            "error": f"cannot cancel job in status {job.status}",
            "job_id": job.id,
            "status": job.status,
        }

    now = _now()
    source_id = job.related_object_id or (job.input_params or {}).get("source_id")
    source = db.get(RawSource, source_id) if source_id else None
    job.status = "cancelled"
    job.completed_at = now
    job.duration_ms = job.duration_ms or _duration_ms(job.started_at, now)
    job.error_message = f"Cancelled: {reason[:500]}"
    job.result_summary = _merge_summary(
        job.result_summary,
        stage="cancelled",
        pct=100,
        message="任务已取消",
        cancelled_at=now.isoformat(),
        cancel_reason=reason[:500],
    )
    job.updated_at = now

    if source and source.status in {"pending", "processing", "failed"}:
        source.status = "failed"
        source.processing_error = f"Cancelled: {reason[:500]}"
        source.updated_at = now

    progress_tracker.cancel_job(job.id, reason)
    safe_commit(db)
    return {
        "success": True,
        "job_id": job.id,
        "status": "cancelled",
        "source_id": source_id,
        "source_status": getattr(source, "status", None),
        "reason": reason[:500],
    }


def recover_orphaned_ingestion_jobs(
    db: Session,
    *,
    limit: int = 3,
    resume: bool = False,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Normalize stale ingestion jobs and optionally resume them.

    The function is intentionally conservative:
    - completed sources close their stale job as completed;
    - missing sources fail the job with an explicit reason;
    - pending/failed/processing sources are reset to pending and optionally resumed via the bounded dispatcher.
    """
    limit = max(0, int(limit or 0))
    resolved_max_retries = _resolve_max_retries(max_retries)
    if limit == 0:
        return {"checked": 0, "recoverable": 0, "resumed": 0, "closed": 0, "failed": 0, "dead_lettered": 0, "jobs": []}

    jobs = (
        db.query(JobRun)
        .filter(JobRun.job_type == "ingestion", JobRun.status.in_(["pending", "running"]))
        .order_by(JobRun.created_at.asc())
        .limit(limit)
        .all()
    )

    stats: dict[str, Any] = {
        "checked": len(jobs),
        "recoverable": 0,
        "resumed": 0,
        "closed": 0,
        "failed": 0,
        "dead_lettered": 0,
        "jobs": [],
    }
    to_resume: list[dict[str, Any]] = []
    recovered_at = _now()

    for job in jobs:
        source_id = job.related_object_id or (job.input_params or {}).get("source_id")
        item: dict[str, Any] = {
            "job_id": job.id,
            "source_id": source_id,
            "previous_job_status": job.status,
        }
        source = db.get(RawSource, source_id) if source_id else None
        item["previous_source_status"] = getattr(source, "status", None)

        if not source:
            job.status = "failed"
            job.completed_at = recovered_at
            job.duration_ms = job.duration_ms or _duration_ms(job.started_at, recovered_at)
            job.error_message = "Recovery failed: related RawSource not found"
            job.result_summary = _merge_summary(
                job.result_summary,
                stage="recovery_failed",
                pct=100,
                message="恢复失败：找不到对应资料",
                recovered_at=recovered_at.isoformat(),
            )
            job.updated_at = recovered_at
            stats["failed"] += 1
            item["action"] = "failed_missing_source"
            stats["jobs"].append(item)
            continue

        if source.status == "completed":
            job.status = "completed"
            job.completed_at = job.completed_at or recovered_at
            job.duration_ms = job.duration_ms or _duration_ms(job.started_at, job.completed_at)
            job.result_summary = _merge_summary(
                job.result_summary,
                stage="completed",
                pct=100,
                message="恢复检查：资料已完成处理",
                recovered_at=recovered_at.isoformat(),
            )
            job.updated_at = recovered_at
            stats["closed"] += 1
            item["action"] = "closed_completed_source"
            stats["jobs"].append(item)
            continue

        if source.status in {"pending", "failed", "processing"}:
            current_retry_count = int(job.retry_count or 0)
            if current_retry_count >= resolved_max_retries:
                job.status = "failed"
                job.completed_at = recovered_at
                job.duration_ms = job.duration_ms or _duration_ms(job.started_at, recovered_at)
                job.error_message = f"Recovery dead letter: retry limit reached ({current_retry_count}/{resolved_max_retries})"
                job.result_summary = _merge_summary(
                    job.result_summary,
                    stage="dead_letter",
                    pct=100,
                    message="恢复失败：已达到最大重试次数",
                    recovered_at=recovered_at.isoformat(),
                    retry_count=current_retry_count,
                    max_retries=resolved_max_retries,
                    previous_source_status=item["previous_source_status"],
                    previous_job_status=item["previous_job_status"],
                )
                job.updated_at = recovered_at
                source.status = "failed"
                source.processing_error = f"Recovery dead letter: retry limit reached ({current_retry_count}/{resolved_max_retries})"
                source.updated_at = recovered_at
                stats["dead_lettered"] += 1
                item["action"] = "dead_letter_retry_limit"
                item["retry_count"] = current_retry_count
                item["max_retries"] = resolved_max_retries
                stats["jobs"].append(item)
                continue

            next_retry_count = current_retry_count + 1 if resume else current_retry_count
            source.status = "pending"
            source.processing_error = None
            source.updated_at = recovered_at
            job.status = "pending"
            job.completed_at = None
            job.duration_ms = None
            job.error_message = None
            job.retry_count = next_retry_count
            job.result_summary = _merge_summary(
                job.result_summary,
                stage="recovered",
                pct=0,
                message="服务启动后已恢复等待处理",
                recovered_at=recovered_at.isoformat(),
                retry_count=next_retry_count,
                max_retries=resolved_max_retries,
                previous_source_status=item["previous_source_status"],
                previous_job_status=item["previous_job_status"],
            )
            job.updated_at = recovered_at
            stats["recoverable"] += 1
            item["action"] = "queued_for_resume" if resume else "reset_to_pending"
            item["retry_count"] = next_retry_count
            item["max_retries"] = resolved_max_retries
            stats["jobs"].append(item)
            if resume:
                to_resume.append(item)
            continue

        job.status = "failed"
        job.completed_at = recovered_at
        job.duration_ms = job.duration_ms or _duration_ms(job.started_at, recovered_at)
        job.error_message = f"Recovery failed: unsupported source status {source.status}"
        job.result_summary = _merge_summary(
            job.result_summary,
            stage="recovery_failed",
            pct=100,
            message=f"恢复失败：资料状态不支持 {source.status}",
            recovered_at=recovered_at.isoformat(),
        )
        job.updated_at = recovered_at
        stats["failed"] += 1
        item["action"] = "failed_unsupported_source_status"
        stats["jobs"].append(item)

    safe_commit(db)

    for item in to_resume:
        source_id = item.get("source_id")
        job_id = item.get("job_id")
        if not source_id or not job_id:
            item["dispatch_status"] = {"submitted": False, "error": "missing source_id or job_id"}
            continue
        try:
            dispatch_status = submit_ingestion_job(str(source_id), str(job_id))
        except Exception as exc:
            dispatch_status = {
                "submitted": False,
                "source_id": source_id,
                "job_id": job_id,
                "dispatcher": "bounded_thread_pool",
                "error": str(exc)[:500],
            }
            logger.warning(f"[IngestionRecovery] dispatcher submit failed job={job_id} source={source_id}: {exc}")
        item["dispatch_status"] = dispatch_status
        if dispatch_status.get("submitted") or dispatch_status.get("reason") == "already_submitted":
            stats["resumed"] += 1

    return stats


def reap_stale_ingestion_jobs(
    db: Session,
    *,
    limit: int = 50,
    resume: bool = True,
    max_retries: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Requeue or dead-letter running ingestion jobs whose DB lease expired."""
    limit = max(0, int(limit or 0))
    checked_at = now or _now()
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    resolved_max_retries = _resolve_max_retries(max_retries)
    if limit == 0:
        return {"checked": 0, "stale": 0, "recoverable": 0, "resumed": 0, "closed": 0, "failed": 0, "dead_lettered": 0, "jobs": []}

    jobs = (
        db.query(JobRun)
        .filter(JobRun.job_type == "ingestion", JobRun.status == "running")
        .order_by(JobRun.updated_at.asc())
        .limit(limit)
        .all()
    )
    stats: dict[str, Any] = {
        "checked": len(jobs),
        "stale": 0,
        "recoverable": 0,
        "resumed": 0,
        "closed": 0,
        "failed": 0,
        "dead_lettered": 0,
        "jobs": [],
    }
    to_resume: list[dict[str, Any]] = []

    for job in jobs:
        summary = job.result_summary if isinstance(job.result_summary, dict) else {}
        source_id = job.related_object_id or (job.input_params or {}).get("source_id")
        item: dict[str, Any] = {
            "job_id": job.id,
            "source_id": source_id,
            "previous_job_status": job.status,
            "previous_lease_state": summary.get("lease_state"),
            "previous_lease_worker_id": summary.get("lease_worker_id"),
            "lease_expires_at": summary.get("lease_expires_at"),
        }
        if not _lease_expired(summary, checked_at):
            item["action"] = "lease_active"
            stats["jobs"].append(item)
            continue

        stats["stale"] += 1
        source = db.get(RawSource, source_id) if source_id else None
        item["previous_source_status"] = getattr(source, "status", None)

        if not source:
            job.status = "failed"
            job.completed_at = checked_at
            job.duration_ms = job.duration_ms or _duration_ms(job.started_at, checked_at)
            job.error_message = "Stale lease recovery failed: related RawSource not found"
            job.result_summary = _merge_summary(
                summary,
                stage="stale_recovery_failed",
                pct=100,
                message="过期 lease 恢复失败：找不到对应资料",
                stale_checked_at=checked_at.isoformat(),
            )
            job.updated_at = checked_at
            stats["failed"] += 1
            item["action"] = "failed_missing_source"
            stats["jobs"].append(item)
            continue

        if source.status == "completed":
            job.status = "completed"
            job.completed_at = job.completed_at or checked_at
            job.duration_ms = job.duration_ms or _duration_ms(job.started_at, job.completed_at)
            job.result_summary = _merge_summary(
                summary,
                stage="completed",
                pct=100,
                message="过期 lease 检查：资料已完成处理",
                stale_checked_at=checked_at.isoformat(),
            )
            job.updated_at = checked_at
            stats["closed"] += 1
            item["action"] = "closed_completed_source"
            stats["jobs"].append(item)
            continue

        current_retry_count = int(job.retry_count or 0)
        if current_retry_count >= resolved_max_retries:
            job.status = "failed"
            job.completed_at = checked_at
            job.duration_ms = job.duration_ms or _duration_ms(job.started_at, checked_at)
            job.error_message = f"Stale lease dead letter: retry limit reached ({current_retry_count}/{resolved_max_retries})"
            job.result_summary = _merge_summary(
                summary,
                stage="dead_letter",
                pct=100,
                message="过期 lease 恢复失败：已达到最大重试次数",
                stale_checked_at=checked_at.isoformat(),
                retry_count=current_retry_count,
                max_retries=resolved_max_retries,
            )
            job.updated_at = checked_at
            source.status = "failed"
            source.processing_error = f"Stale lease dead letter: retry limit reached ({current_retry_count}/{resolved_max_retries})"
            source.updated_at = checked_at
            stats["dead_lettered"] += 1
            item["action"] = "dead_letter_retry_limit"
            item["retry_count"] = current_retry_count
            item["max_retries"] = resolved_max_retries
            stats["jobs"].append(item)
            continue

        next_retry_count = current_retry_count + 1 if resume else current_retry_count
        source.status = "pending"
        source.processing_error = None
        source.updated_at = checked_at
        job.status = "pending"
        job.completed_at = None
        job.duration_ms = None
        job.error_message = None
        job.retry_count = next_retry_count
        job.result_summary = _merge_summary(
            summary,
            stage="stale_recovered",
            pct=0,
            message="过期 lease 已恢复等待处理",
            stale_checked_at=checked_at.isoformat(),
            retry_count=next_retry_count,
            max_retries=resolved_max_retries,
            previous_source_status=item["previous_source_status"],
            previous_job_status=item["previous_job_status"],
            previous_lease_state=item["previous_lease_state"],
            previous_lease_worker_id=item["previous_lease_worker_id"],
        )
        job.updated_at = checked_at
        stats["recoverable"] += 1
        item["action"] = "queued_for_resume" if resume else "reset_to_pending"
        item["retry_count"] = next_retry_count
        item["max_retries"] = resolved_max_retries
        stats["jobs"].append(item)
        if resume:
            to_resume.append(item)

    safe_commit(db)

    for item in to_resume:
        source_id = item.get("source_id")
        job_id = item.get("job_id")
        if not source_id or not job_id:
            item["dispatch_status"] = {"submitted": False, "error": "missing source_id or job_id"}
            continue
        try:
            dispatch_status = submit_ingestion_job(str(source_id), str(job_id))
        except Exception as exc:
            dispatch_status = {
                "submitted": False,
                "source_id": source_id,
                "job_id": job_id,
                "dispatcher": "bounded_thread_pool",
                "error": str(exc)[:500],
            }
            logger.warning(f"[IngestionRecovery] stale lease dispatcher submit failed job={job_id} source={source_id}: {exc}")
        item["dispatch_status"] = dispatch_status
        if dispatch_status.get("submitted") or dispatch_status.get("reason") == "already_submitted":
            stats["resumed"] += 1

    return stats
