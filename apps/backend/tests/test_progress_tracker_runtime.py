import time
from datetime import datetime, timedelta, timezone

import pytest

from app.api.v1.progress import _progress_from_job_run
from app.schemas.job_run import JobRun
from app.services.progress_tracker import ProgressTracker


def test_running_stage_reports_live_elapsed_ms():
    tracker = ProgressTracker()
    tracker.start_job("job-live-elapsed", "ingestion")
    tracker.update_stage("job-live-elapsed", "load", "completed")
    tracker.update_stage("job-live-elapsed", "parse", "running", "正在解析内容")

    time.sleep(0.02)

    progress = tracker.get_progress("job-live-elapsed")
    parse_stage = next(stage for stage in progress["stages"] if stage["key"] == "parse")

    assert progress["current_stage"] == "parse"
    assert progress["current_stage_elapsed_ms"] >= 10
    assert parse_stage["elapsed_ms"] >= 10


def test_stage_completed_without_running_reports_zero_elapsed_ms():
    tracker = ProgressTracker()
    tracker.start_job("job-instant-stage", "ingestion")
    tracker.update_stage("job-instant-stage", "parse", "completed", "解析瞬时完成")

    progress = tracker.get_progress("job-instant-stage")
    parse_stage = next(stage for stage in progress["stages"] if stage["key"] == "parse")

    assert parse_stage["status"] == "completed"
    assert parse_stage["elapsed_ms"] == 0


def test_running_stage_recovers_missing_started_at():
    tracker = ProgressTracker()
    tracker.start_job("job-reopened-stage", "ingestion")
    tracker.update_stage("job-reopened-stage", "extraction", "completed", "瞬时完成")
    tracker.update_stage("job-reopened-stage", "extraction", "running", "重新进入抽取")

    time.sleep(0.02)

    progress = tracker.get_progress("job-reopened-stage")
    extraction_stage = next(stage for stage in progress["stages"] if stage["key"] == "extraction")

    assert progress["current_stage"] == "extraction"
    assert progress["current_stage_elapsed_ms"] >= 10
    assert extraction_stage["elapsed_ms"] >= 10


def test_running_progress_never_returns_null_elapsed_when_stage_started_at_missing():
    tracker = ProgressTracker()
    tracker.start_job("job-missing-stage-start", "ingestion")
    tracker.update_stage("job-missing-stage-start", "extraction", "running", "正在抽取")

    with tracker._lock:
        job = tracker._jobs["job-missing-stage-start"]
        next(stage for stage in job.stages if stage.key == "extraction").started_at = None

    progress = tracker.get_progress("job-missing-stage-start")
    extraction_stage = next(stage for stage in progress["stages"] if stage["key"] == "extraction")

    assert progress["current_stage"] == "extraction"
    assert isinstance(progress["current_stage_elapsed_ms"], int)
    assert isinstance(extraction_stage["elapsed_ms"], int)


def test_started_job_without_stage_update_reports_live_elapsed_ms():
    tracker = ProgressTracker()
    tracker.start_job("job-queued-before-worker", "ingestion", source_id="source-queued-1")

    time.sleep(0.02)

    progress = tracker.get_progress("job-queued-before-worker")
    load_stage = next(stage for stage in progress["stages"] if stage["key"] == "load")

    assert progress["status"] == "running"
    assert progress["current_stage"] == "load"
    assert isinstance(progress["current_stage_elapsed_ms"], int)
    assert progress["current_stage_elapsed_ms"] >= 10
    assert load_stage["status"] == "running"
    assert load_stage["elapsed_ms"] >= 10


def test_progress_tracker_preserves_source_scope():
    tracker = ProgressTracker()
    tracker.start_job("job-scoped-progress", "ingestion", source_id="source-scoped-1")

    progress = tracker.get_progress("job-scoped-progress")

    assert progress["source_id"] == "source-scoped-1"
    assert progress["source_ids"] == ["source-scoped-1"]


def test_progress_tracker_preserves_multi_source_object_scope():
    tracker = ProgressTracker()
    tracker.start_job(
        "job-multi-source-progress",
        "committee_debate",
        source_ids=["source-a", "source-b", "source-a"],
        object_scope={"asset": "BTC", "source_ids": ["source-a", "source-b"]},
        related_object_id="debate-1",
        related_object_type="debate_record",
    )

    progress = tracker.get_progress("job-multi-source-progress")

    assert progress["source_id"] == "source-a"
    assert progress["source_ids"] == ["source-a", "source-b"]
    assert progress["object_scope"] == {"asset": "BTC", "source_ids": ["source-a", "source-b"]}
    assert progress["related_object_id"] == "debate-1"
    assert progress["related_object_type"] == "debate_record"


def test_job_run_fallback_progress_reports_running_stage_elapsed_ms():
    started_at = datetime.now(timezone.utc) - timedelta(seconds=3)
    job = JobRun(
        id="job-fallback-live-elapsed",
        job_type="ingestion",
        status="running",
        result_summary={
            "stage": "extraction",
            "pct": 37,
            "message": "正在抽取观点",
        },
        started_at=started_at,
        created_at=started_at,
        updated_at=started_at,
    )

    progress = _progress_from_job_run(job)
    extraction_stage = next(stage for stage in progress["stages"] if stage["key"] == "extraction")

    assert progress["current_stage"] == "extraction"
    assert progress["current_stage_elapsed_ms"] >= 2500
    assert extraction_stage["status"] == "running"
    assert extraction_stage["elapsed_ms"] >= 2500


@pytest.mark.asyncio
async def test_progress_endpoint_fills_memory_running_elapsed_from_job_run(db, monkeypatch):
    from app.api.v1.progress import get_job_progress
    from app.services.progress_tracker import progress_tracker

    started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    job = JobRun(
        id="job-memory-api-live-elapsed",
        job_type="ingestion",
        status="running",
        result_summary={"stage": "extraction", "pct": 37, "message": "正在抽取观点"},
        started_at=started_at,
        created_at=started_at,
        updated_at=started_at,
    )
    db.add(job)
    db.commit()

    monkeypatch.setattr(
        progress_tracker,
        "get_progress",
        lambda _job_id: {
            "job_id": job.id,
            "workflow": "ingestion",
            "status": "running",
            "current_stage": "extraction",
            "current_stage_label": "提取观点",
            "current_stage_elapsed_ms": None,
            "stages": [
                {"key": "extraction", "label": "提取观点", "status": "running", "elapsed_ms": None, "detail": None}
            ],
            "progress_pct": 37,
            "started_at": started_at.isoformat(),
            "elapsed_ms": None,
            "message": "正在抽取观点",
            "error": None,
        },
    )

    progress = await get_job_progress(job.id, db=db)
    extraction_stage = progress["stages"][0]

    assert isinstance(progress["current_stage_elapsed_ms"], int)
    assert progress["current_stage_elapsed_ms"] >= 1500
    assert extraction_stage["elapsed_ms"] >= 1500
