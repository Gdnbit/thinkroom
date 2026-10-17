"""Ingestion 触发端点 — 异步后台处理"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from ulid import ULID
from app.dependencies import get_db
from app.config import settings
from app.workflows.ingestion_workflow import IngestionWorkflow
from app.database import SessionLocal, safe_commit
from app.schemas.job_run import JobRun
from app.schemas.raw_source import RawSource
from app.utils.text_stats import build_source_status_payload
from app.utils.parse_diagnostics import decode_processing_error
from app.services.progress_tracker import progress_tracker
from app.services.ingestion_dispatcher import (
    list_inflight_ingestion_jobs,
    release_ingestion_capacity,
    reserve_ingestion_capacity,
    submit_ingestion_job,
)

router = APIRouter()


def _run_ingestion(source_id: str, job_id: str | None = None):
    """在后台线程中处理单个 source（独立 DB session）"""
    db = SessionLocal()
    try:
        workflow = IngestionWorkflow(db)
        workflow.process_source(source_id, job_id=job_id)
    finally:
        db.close()


def _run_batch(limit: int):
    """在后台线程中批量处理 pending sources"""
    db = SessionLocal()
    try:
        workflow = IngestionWorkflow(db)
        workflow.process_pending_batch(limit=limit)
    finally:
        db.close()


def _create_queued_ingestion_job(
    db: Session,
    source_id: str,
    session_id: str | None = None,
    *,
    commit: bool = True,
) -> JobRun:
    """Persist the async contract before handing work to BackgroundTasks."""
    now = datetime.now(timezone.utc)
    input_params = {"source_id": source_id, "async_mode": True}
    object_scope = {"source_ids": [source_id]}
    if session_id:
        input_params["session_id"] = session_id
        object_scope["session_id"] = session_id
    job = JobRun(
        id=str(ULID()),
        job_type="ingestion",
        status="pending",
        triggered_by="api",
        input_params=input_params,
        result_summary={
            "stage": "queued",
            "pct": 0,
            "message": "等待后台解析资料",
            "source_id": source_id,
            **({"session_id": session_id} if session_id else {}),
        },
        session_id=session_id,
        source_ids=[source_id],
        object_scope=object_scope,
        related_object_id=source_id,
        related_object_type="raw_source",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    if commit:
        safe_commit(db)
        db.refresh(job)
    return job


def _start_queued_ingestion_progress(job: JobRun, source_id: str, session_id: str | None = None) -> str:
    object_scope = {"source_ids": [source_id], **({"session_id": session_id} if session_id else {})}
    progress_tracker.start_job(
        job.id,
        "ingestion",
        source_id=source_id,
        session_id=session_id,
        object_scope=object_scope,
        related_object_id=source_id,
        related_object_type="raw_source",
    )
    return f"/api/v1/progress/{job.id}"


def _reserve_async_capacity_or_raise(db: Session, count: int) -> dict:
    requested_count = max(0, int(count or 0))
    persisted_active_count = (
        db.query(JobRun)
        .filter(JobRun.job_type == "ingestion", JobRun.status.in_(["pending", "running"]))
        .count()
    )
    max_active_jobs = max(1, int(getattr(settings, "INGESTION_MAX_ACTIVE_JOBS", 100) or 100))
    if persisted_active_count + requested_count > max_active_jobs:
        raise HTTPException(
            status_code=429,
            detail={
                "reason": "persisted_capacity_exceeded",
                "retryable": True,
                "requested_count": requested_count,
                "persisted_active_count": persisted_active_count,
                "max_active_jobs": max_active_jobs,
                "available_capacity": max(0, max_active_jobs - persisted_active_count),
            },
        )
    reservation = reserve_ingestion_capacity(requested_count)
    if not reservation.get("reserved"):
        raise HTTPException(status_code=429, detail=reservation)
    return {
        **reservation,
        "persisted_active_count": persisted_active_count,
        "max_active_jobs": max_active_jobs,
    }


def _dispatch_queued_ingestion_jobs(
    queued_jobs: list[dict],
    *,
    reservation_id: str | None = None,
) -> list[dict]:
    jobs = []
    try:
        for item in queued_jobs:
            dispatch_status = submit_ingestion_job(
                item["source_id"],
                item["job_id"],
                reservation_id=reservation_id,
            )
            jobs.append({**item, "dispatch_status": dispatch_status})
    finally:
        release_ingestion_capacity(reservation_id)
    return jobs


def _persist_dispatch_failures(db: Session, jobs: list[dict]) -> None:
    failed = [
        item
        for item in jobs
        if not item.get("dispatch_status", {}).get("submitted")
        and item.get("dispatch_status", {}).get("reason") != "already_submitted"
    ]
    if not failed:
        return
    now = datetime.now(timezone.utc)
    for item in failed:
        job = db.get(JobRun, item["job_id"])
        if not job or job.status in {"completed", "failed", "cancelled"}:
            continue
        dispatch_status = item.get("dispatch_status") or {}
        job.status = "failed"
        job.completed_at = now
        job.error_message = f"Dispatch failed: {dispatch_status.get('reason') or 'unknown'}"
        job.result_summary = {
            **(job.result_summary or {}),
            "stage": "dispatch_failed",
            "pct": 100,
            "message": "后台处理任务未能进入执行队列",
            "dispatch_reason": dispatch_status.get("reason") or "unknown",
            "retryable": bool(dispatch_status.get("retryable")),
        }
        job.updated_at = now
    safe_commit(db)


def _parse_iso_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _latest_parse_diagnostics(db: Session, source_id: str) -> dict | None:
    job = (
        db.query(JobRun)
        .filter(
            JobRun.job_type == "ingestion",
            JobRun.related_object_id == source_id,
        )
        .order_by(JobRun.created_at.desc())
        .first()
    )
    summary = job.result_summary if job else None
    if not isinstance(summary, dict):
        return None
    diagnostics = summary.get("parse_diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else None


@router.post("/process/{source_id}")
async def process_source(
    source_id: str,
    background_tasks: BackgroundTasks,
    async_mode: bool = False,
    session_id: str | None = None,
    db: Session = Depends(get_db),
):
    """触发单个 RawSource 的 Claim 抽取。默认同步返回结果；可切换后台异步。"""
    workflow = IngestionWorkflow(db)

    source = workflow.raw_source_repo.get(source_id)
    if not source:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Source {source_id} not found")
    if source.status not in ("pending", "failed"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Source already {source.status}")

    if async_mode:
        if session_id:
            from app.schemas.chat import ChatSession

            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")

        admission = _reserve_async_capacity_or_raise(db, 1)
        reservation_id = admission.get("reservation_id")
        try:
            job = _create_queued_ingestion_job(db, source_id, session_id=session_id)
            progress_url = _start_queued_ingestion_progress(job, source_id, session_id=session_id)
            jobs = _dispatch_queued_ingestion_jobs(
                [{"source_id": source_id, "job_id": job.id, "progress_url": progress_url}],
                reservation_id=reservation_id,
            )
        except Exception:
            release_ingestion_capacity(reservation_id)
            raise
        dispatch_status = jobs[0]["dispatch_status"]
        _persist_dispatch_failures(db, jobs)
        if not dispatch_status.get("submitted") and dispatch_status.get("reason") != "already_submitted":
            raise HTTPException(
                status_code=503,
                detail={
                    "reason": dispatch_status.get("reason") or "dispatch_failed",
                    "retryable": bool(dispatch_status.get("retryable")),
                    "source_id": source_id,
                    "job_id": job.id,
                },
            )
        return {
            "queued": True,
            "source_id": source_id,
            "job_id": job.id,
            "session_id": session_id,
            "progress_url": progress_url,
            "dispatch_status": dispatch_status,
            "admission": {key: value for key, value in admission.items() if key != "reservation_id"},
            "message": "Processing started in background",
        }

    return workflow.process_source(source_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    payload: dict | None = None,
    db: Session = Depends(get_db),
):
    """取消排队或运行中的资料入库任务。"""
    from app.services.ingestion_job_recovery import cancel_ingestion_job

    reason = "用户取消"
    if isinstance(payload, dict) and payload.get("reason"):
        reason = str(payload.get("reason"))
    result = cancel_ingestion_job(db, job_id, reason=reason)
    if not result.get("success"):
        raise HTTPException(status_code=int(result.get("status_code") or 400), detail=result.get("error"))
    return result


@router.get("/dispatcher/status")
async def get_dispatcher_status(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Return runtime and persisted ingestion queue diagnostics."""
    runtime_status = list_inflight_ingestion_jobs()
    active_jobs = (
        db.query(JobRun)
        .filter(JobRun.job_type == "ingestion", JobRun.status.in_(["pending", "running"]))
        .order_by(JobRun.created_at.asc())
        .limit(max(1, min(int(limit or 50), 200)))
        .all()
    )

    persisted_active_jobs = []
    now = datetime.now(timezone.utc)
    for job in active_jobs:
        input_params = job.input_params if isinstance(job.input_params, dict) else {}
        source_id = job.related_object_id or input_params.get("source_id")
        summary = job.result_summary if isinstance(job.result_summary, dict) else {}
        lease_expires_at = summary.get("lease_expires_at")
        parsed_lease_expires_at = _parse_iso_datetime(lease_expires_at)
        persisted_active_jobs.append(
            {
                "job_id": job.id,
                "source_id": source_id,
                "status": job.status,
                "stage": summary.get("stage"),
                "pct": summary.get("pct"),
                "message": summary.get("message"),
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
                "retry_count": job.retry_count,
                "lease_state": summary.get("lease_state"),
                "lease_worker_id": summary.get("lease_worker_id"),
                "lease_heartbeat_at": summary.get("lease_heartbeat_at"),
                "lease_expires_at": lease_expires_at,
                "lease_seconds": summary.get("lease_seconds"),
                "lease_expired": bool(parsed_lease_expires_at and parsed_lease_expires_at <= now),
            }
        )

    return {
        **runtime_status,
        "persisted_active_count": len(persisted_active_jobs),
        "persisted_active_job_ids": [item["job_id"] for item in persisted_active_jobs],
        "persisted_active_jobs": persisted_active_jobs,
    }


@router.post("/dispatcher/reap-stale")
async def reap_stale_dispatcher_jobs(
    limit: int = 50,
    resume: bool = True,
    max_retries: int | None = None,
    db: Session = Depends(get_db),
):
    """Requeue or dead-letter running ingestion jobs whose DB lease expired."""
    from app.services.ingestion_job_recovery import reap_stale_ingestion_jobs

    return reap_stale_ingestion_jobs(
        db,
        limit=max(1, min(int(limit or 50), 200)),
        resume=resume,
        max_retries=max_retries,
    )


@router.get("/status/{source_id}")
async def get_source_status(
    source_id: str,
    db: Session = Depends(get_db),
):
    """查询单个 RawSource 的处理状态"""
    from app.services.raw_source_repository import RawSourceRepository
    from app.schemas.claim_card import ClaimRawSourceLink
    repo = RawSourceRepository(db)
    source = repo.get(source_id)
    if not source:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Source not found")
    claim_count = db.query(ClaimRawSourceLink).filter(ClaimRawSourceLink.source_id == source.id).count()
    error_message, parse_diagnostics = decode_processing_error(source.processing_error)
    if parse_diagnostics is None:
        parse_diagnostics = _latest_parse_diagnostics(db, source.id)
    return {
        "source_id": source.id,
        "title": source.title,
        "status": source.status,
        "error": error_message,
        "parse_diagnostics": parse_diagnostics,
        "word_count": source.word_count,
        **build_source_status_payload(source, claim_count),
    }


@router.post("/status/batch")
async def get_batch_status(
    source_ids: list[str],
    db: Session = Depends(get_db),
):
    """批量查询 RawSource 处理状态"""
    from app.schemas.raw_source import RawSource as RS
    from app.schemas.claim_card import ClaimRawSourceLink
    sources = db.query(RS).filter(RS.id.in_(source_ids)).all()
    claim_counts = dict(
        db.query(ClaimRawSourceLink.source_id, func.count(ClaimRawSourceLink.claim_id))
        .filter(ClaimRawSourceLink.source_id.in_(source_ids))
        .group_by(ClaimRawSourceLink.source_id)
        .all()
    ) if source_ids else {}
    result = []
    for s in sources:
        error_message, parse_diagnostics = decode_processing_error(s.processing_error)
        if parse_diagnostics is None:
            parse_diagnostics = _latest_parse_diagnostics(db, s.id)
        result.append({
            "source_id": s.id,
            "title": s.title,
            "status": s.status,
            "error": error_message,
            "parse_diagnostics": parse_diagnostics,
            "word_count": s.word_count,
            **build_source_status_payload(s, int(claim_counts.get(s.id, 0))),
        })
    return result


@router.post("/process-selected")
async def process_selected_sources(
    payload: dict,
    db: Session = Depends(get_db),
):
    """Queue only explicitly selected RawSources for ingestion."""
    source_ids_raw = payload.get("source_ids") if isinstance(payload, dict) else None
    if not isinstance(source_ids_raw, list):
        raise HTTPException(status_code=400, detail="source_ids must be a list")

    source_ids: list[str] = []
    for source_id in source_ids_raw:
        value = str(source_id).strip()
        if value and value not in source_ids:
            source_ids.append(value)

    if not source_ids:
        raise HTTPException(status_code=400, detail="source_ids cannot be empty")
    if len(source_ids) > 50:
        raise HTTPException(status_code=400, detail="source_ids cannot exceed 50")

    async_mode = bool(payload.get("async_mode", True))
    if not async_mode:
        raise HTTPException(status_code=400, detail="process-selected currently supports async_mode=true only")

    session_id = payload.get("session_id")
    if session_id:
        from app.schemas.chat import ChatSession

        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    sources = db.query(RawSource).filter(RawSource.id.in_(source_ids)).all()
    sources_by_id = {source.id: source for source in sources}
    missing_source_ids = [source_id for source_id in source_ids if source_id not in sources_by_id]
    if missing_source_ids:
        raise HTTPException(status_code=404, detail={"missing_source_ids": missing_source_ids})

    invalid_sources = [
        {"source_id": source_id, "status": sources_by_id[source_id].status}
        for source_id in source_ids
        if sources_by_id[source_id].status not in ("pending", "failed")
    ]
    if invalid_sources:
        raise HTTPException(status_code=400, detail={"invalid_sources": invalid_sources})

    admission = _reserve_async_capacity_or_raise(db, len(source_ids))
    reservation_id = admission.get("reservation_id")
    try:
        pending_jobs = [
            _create_queued_ingestion_job(db, source_id, session_id=session_id, commit=False)
            for source_id in source_ids
        ]
        safe_commit(db)
        for job in pending_jobs:
            db.refresh(job)
        queued_jobs = [
            {
                "source_id": source_id,
                "job_id": job.id,
                "progress_url": _start_queued_ingestion_progress(job, source_id, session_id=session_id),
            }
            for source_id, job in zip(source_ids, pending_jobs)
        ]
        jobs = _dispatch_queued_ingestion_jobs(queued_jobs, reservation_id=reservation_id)
    except Exception:
        db.rollback()
        release_ingestion_capacity(reservation_id)
        raise
    _persist_dispatch_failures(db, jobs)
    return {
        "queued": True,
        "limit": len(source_ids),
        "queued_count": len(jobs),
        "source_ids": [item["source_id"] for item in jobs],
        "job_ids": [item["job_id"] for item in jobs],
        "jobs": jobs,
        "session_id": session_id,
        "admission": {key: value for key, value in admission.items() if key != "reservation_id"},
        "message": "Selected sources processing started in background",
    }


@router.post("/process-batch")
async def process_batch(
    background_tasks: BackgroundTasks,
    limit: int = 5,
    async_mode: bool = False,
    db: Session = Depends(get_db),
):
    """批量处理 pending RawSource。默认同步返回结果；可切换后台异步。"""
    if async_mode:
        pending_sources = (
            db.query(RawSource)
            .filter(RawSource.status == "pending")
            .order_by(RawSource.created_at.asc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        admission = _reserve_async_capacity_or_raise(db, len(pending_sources))
        reservation_id = admission.get("reservation_id")
        try:
            pending_jobs = [
                _create_queued_ingestion_job(db, source.id, commit=False)
                for source in pending_sources
            ]
            safe_commit(db)
            for job in pending_jobs:
                db.refresh(job)
            queued_jobs = [
                {
                    "source_id": source.id,
                    "job_id": job.id,
                    "progress_url": _start_queued_ingestion_progress(job, source.id),
                }
                for source, job in zip(pending_sources, pending_jobs)
            ]
            jobs = _dispatch_queued_ingestion_jobs(queued_jobs, reservation_id=reservation_id)
        except Exception:
            db.rollback()
            release_ingestion_capacity(reservation_id)
            raise
        _persist_dispatch_failures(db, jobs)
        return {
            "queued": True,
            "limit": limit,
            "queued_count": len(jobs),
            "source_ids": [item["source_id"] for item in jobs],
            "job_ids": [item["job_id"] for item in jobs],
            "jobs": jobs,
            "admission": {key: value for key, value in admission.items() if key != "reservation_id"},
            "message": "Batch processing started in background",
        }

    workflow = IngestionWorkflow(db)
    return workflow.process_pending_batch(limit=limit)
