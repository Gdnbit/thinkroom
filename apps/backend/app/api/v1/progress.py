"""Progress API — 实时进度查询

端点：
- GET /progress/{job_id}  — 查询某个 Job 的实时进度
- GET /progress/active     — 列出所有活跃 Job 的进度
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.job_run import JobRun
from app.services.progress_tracker import WORKFLOW_STAGES, progress_tracker
from app.utils.job_scope import job_source_id, job_source_ids, source_ids_from_mapping

router = APIRouter()


def _job_elapsed_ms(job: JobRun) -> int:
    now = datetime.now(timezone.utc)
    started_at = job.started_at or job.created_at or now
    if started_at and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if job.duration_ms is not None and job.status in {"completed", "failed", "cancelled"}:
        return int(job.duration_ms)
    return max(0, int((now - started_at).total_seconds() * 1000)) if started_at else 0


def _fill_fallback_running_elapsed(payload: dict, job: JobRun) -> dict:
    if job.status != "running":
        return payload
    current_stage = payload.get("current_stage")
    if not current_stage:
        return payload

    elapsed_ms = payload.get("current_stage_elapsed_ms")
    if not isinstance(elapsed_ms, int):
        elapsed_ms = _job_elapsed_ms(job)
        payload["current_stage_elapsed_ms"] = elapsed_ms

    stages = payload.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            if stage.get("key") == current_stage and stage.get("status") == "running" and stage.get("elapsed_ms") is None:
                stage["elapsed_ms"] = elapsed_ms
    return payload


def _fallback_stage_rows(workflow: str, current_stage: str | None, job_status: str) -> tuple[list[dict], str | None, str]:
    stage_defs = WORKFLOW_STAGES.get(workflow, [{"key": "processing", "label": "处理中"}])
    keys = [stage["key"] for stage in stage_defs]
    active_stage = current_stage if current_stage in keys else None
    if active_stage is None and job_status in {"pending", "running"} and keys:
        active_stage = keys[0]
    active_index = keys.index(active_stage) if active_stage in keys else -1
    current_label = ""
    rows: list[dict] = []

    for index, stage in enumerate(stage_defs):
        key = stage["key"]
        label = stage["label"]
        if job_status == "completed":
            status = "completed"
        elif job_status in {"failed", "cancelled"}:
            if active_index >= 0 and index < active_index:
                status = "completed"
            elif active_index >= 0 and index == active_index:
                status = "failed"
            else:
                status = "skipped"
        elif active_index >= 0:
            if index < active_index:
                status = "completed"
            elif index == active_index:
                status = "running" if job_status == "running" else "pending"
                current_label = label
            else:
                status = "pending"
        else:
            status = "pending"
        rows.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "elapsed_ms": None,
                "detail": None,
            }
        )

    return rows, active_stage, current_label


def _progress_from_job_run(job: JobRun) -> dict:
    summary = job.result_summary or {}
    snapshot = summary.get("progress_snapshot")
    if isinstance(snapshot, dict):
        restored = dict(snapshot)
        restored["status"] = job.status
        restored["source"] = "job_run"
        restored["result_summary"] = summary
        restored["error"] = job.error_message or restored.get("error")
        if job.duration_ms is not None:
            restored["elapsed_ms"] = job.duration_ms
        if job.status == "completed":
            restored["progress_pct"] = 100
            restored["current_stage"] = None
            restored["current_stage_label"] = ""
        return _with_job_scope(_fill_fallback_running_elapsed(restored, job), job)

    stage = summary.get("stage")
    pct = summary.get("pct")
    if job.status == "completed":
        pct = 100
    elif job.status in {"failed", "cancelled"}:
        pct = pct or 100
    elif pct is None:
        pct = 5 if job.status == "running" else 0

    now = datetime.now(timezone.utc)
    started_at = job.started_at or job.created_at or now
    if started_at and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed_ms = job.duration_ms
    if elapsed_ms is None and started_at:
        elapsed_ms = _job_elapsed_ms(job)

    stages, current_stage, current_label = _fallback_stage_rows(job.job_type, stage, job.status)

    payload = {
        "job_id": job.id,
        "workflow": job.job_type,
        "status": job.status,
        "current_stage": current_stage or stage,
        "current_stage_label": current_label or current_stage or stage or job.status,
        "stages": stages,
        "progress_pct": int(pct or 0),
        "started_at": started_at.isoformat() if started_at else None,
        "elapsed_ms": elapsed_ms or 0,
        "message": summary.get("message") or summary.get("note") or job.status,
        "error": job.error_message,
        "source": "job_run",
        "result_summary": summary,
    }
    return _with_job_scope(_fill_fallback_running_elapsed(payload, job), job)


def _with_job_scope(payload: dict, job: JobRun | None) -> dict:
    """Attach persisted object scope to a progress payload."""
    if job is None:
        return payload
    source_id = job_source_id(job)
    source_ids = job_source_ids(job)
    if source_id:
        payload["source_id"] = source_id
    if source_ids:
        payload["source_ids"] = source_ids
    if job.session_id:
        payload["session_id"] = job.session_id
    if job.object_scope:
        payload["object_scope"] = job.object_scope
    if job.related_object_id:
        payload["related_object_id"] = job.related_object_id
    if job.related_object_type:
        payload["related_object_type"] = job.related_object_type
    return payload


def _progress_source_ids(payload: dict | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    source_ids = source_ids_from_mapping(payload)
    if source_ids:
        return source_ids
    summary = payload.get("result_summary")
    source_ids = source_ids_from_mapping(summary)
    if source_ids:
        return source_ids
    if isinstance(summary, dict):
        return source_ids_from_mapping(summary.get("progress_snapshot"))
    return []


def _enforce_expected_source(job: JobRun | None, payload: dict | None, expected_source_id: str | None) -> None:
    if not expected_source_id:
        return
    expected = expected_source_id.strip()
    if not expected:
        return
    actual_source_ids = job_source_ids(job) or _progress_source_ids(payload)
    if expected not in actual_source_ids:
        detail = "Job source scope unavailable" if not actual_source_ids else "Job source mismatch"
        raise HTTPException(status_code=403, detail=detail)


@router.get("/active", response_model=list)
async def list_active_jobs(db: Session = Depends(get_db)):
    """列出所有正在运行或等待恢复的 Job 的进度。"""
    memory_jobs = []
    seen_ids = set()
    for item in progress_tracker.list_active():
        if not item:
            continue
        current = dict(item)
        current.setdefault("source", "memory")
        job_id = current.get("job_id")
        if job_id:
            seen_ids.add(job_id)
        memory_jobs.append(current)

    persisted_jobs = (
        db.query(JobRun)
        .filter(JobRun.status.in_(["pending", "running"]))
        .order_by(JobRun.created_at.desc())
        .limit(50)
        .all()
    )
    restored_jobs = [
        _progress_from_job_run(job)
        for job in persisted_jobs
        if job.id not in seen_ids
    ]
    return memory_jobs + restored_jobs


@router.get("/{job_id}", response_model=dict)
async def get_job_progress(
    job_id: str,
    source_id: str | None = None,
    db: Session = Depends(get_db),
):
    """查询某个 Job 的实时阶段进度"""
    result = progress_tracker.get_progress(job_id)
    if result is not None:
        job = db.get(JobRun, job_id)
        _enforce_expected_source(job, result, source_id)
        if job is not None:
            result = dict(result)
            if isinstance(job.result_summary, dict):
                result["result_summary"] = job.result_summary
            if job.error_message and not result.get("error"):
                result["error"] = job.error_message
            result = _with_job_scope(_fill_fallback_running_elapsed(result, job), job)
        return result
    job = db.get(JobRun, job_id)
    if job is not None:
        result = _progress_from_job_run(job)
        _enforce_expected_source(job, result, source_id)
        return result
    return JSONResponse(status_code=404, content={"detail": f"Job {job_id} not found"})
