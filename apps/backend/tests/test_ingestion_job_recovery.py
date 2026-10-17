from datetime import datetime, timedelta, timezone

import pytest
from ulid import ULID


def _make_source(db, *, status: str = "processing"):
    from app.schemas.raw_source import RawSource

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="recovery fixture",
        raw_text="恢复测试资料",
        language="zh",
        status=status,
        processing_error="previous error" if status == "failed" else None,
        word_count=6,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _make_job(db, source_id: str | None, *, status: str = "running", result_summary: dict | None = None):
    from app.schemas.job_run import JobRun

    now = datetime.now(timezone.utc)
    job = JobRun(
        id=str(ULID()),
        job_type="ingestion",
        status=status,
        triggered_by="api",
        input_params={"source_id": source_id} if source_id else {},
        result_summary=result_summary or {"stage": "parse", "pct": 20, "message": "stale"},
        started_at=now,
        related_object_id=source_id,
        related_object_type="raw_source",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_recovery_resets_processing_source_and_job_to_pending(db):
    from app.services.ingestion_job_recovery import recover_orphaned_ingestion_jobs

    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")

    stats = recover_orphaned_ingestion_jobs(db, limit=5, resume=False)

    db.refresh(source)
    db.refresh(job)
    assert stats["checked"] == 1
    assert stats["recoverable"] == 1
    assert stats["resumed"] == 0
    assert source.status == "pending"
    assert source.processing_error is None
    assert job.status == "pending"
    assert job.completed_at is None
    assert job.result_summary["stage"] == "recovered"
    assert job.result_summary["previous_source_status"] == "processing"


def test_recovery_resume_uses_bounded_dispatcher(db, monkeypatch):
    from app.services.ingestion_job_recovery import recover_orphaned_ingestion_jobs

    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")
    submitted: list[tuple[str, str]] = []

    def fake_submit(source_id: str, job_id: str):
        submitted.append((source_id, job_id))
        return {
            "submitted": True,
            "source_id": source_id,
            "job_id": job_id,
            "dispatcher": "bounded_thread_pool",
            "max_workers": 2,
            "active_or_queued_count": 1,
        }

    monkeypatch.setattr("app.services.ingestion_job_recovery.submit_ingestion_job", fake_submit, raising=False)

    stats = recover_orphaned_ingestion_jobs(db, limit=5, resume=True)

    assert stats["checked"] == 1
    assert stats["recoverable"] == 1
    assert stats["resumed"] == 1
    assert submitted == [(source.id, job.id)]
    assert stats["jobs"][0]["action"] == "queued_for_resume"
    assert stats["jobs"][0]["dispatch_status"]["dispatcher"] == "bounded_thread_pool"
    db.refresh(job)
    assert job.retry_count == 1
    assert job.result_summary["retry_count"] == 1


def test_recovery_dead_letters_job_when_retry_limit_reached(db, monkeypatch):
    from app.services.ingestion_job_recovery import recover_orphaned_ingestion_jobs

    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")
    job.retry_count = 2
    db.commit()
    submitted: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "app.services.ingestion_job_recovery.submit_ingestion_job",
        lambda source_id, job_id: submitted.append((source_id, job_id)),
        raising=False,
    )

    stats = recover_orphaned_ingestion_jobs(db, limit=5, resume=True, max_retries=2)

    db.refresh(source)
    db.refresh(job)
    assert stats["checked"] == 1
    assert stats["recoverable"] == 0
    assert stats["resumed"] == 0
    assert stats["dead_lettered"] == 1
    assert submitted == []
    assert job.status == "failed"
    assert job.retry_count == 2
    assert job.result_summary["stage"] == "dead_letter"
    assert job.result_summary["retry_count"] == 2
    assert job.result_summary["max_retries"] == 2
    assert source.status == "failed"
    assert "retry limit" in source.processing_error


def test_recovery_closes_stale_job_when_source_already_completed(db):
    from app.services.ingestion_job_recovery import recover_orphaned_ingestion_jobs

    source = _make_source(db, status="completed")
    job = _make_job(db, source.id, status="running")

    stats = recover_orphaned_ingestion_jobs(db, limit=5, resume=False)

    db.refresh(job)
    assert stats["checked"] == 1
    assert stats["closed"] == 1
    assert job.status == "completed"
    assert job.completed_at is not None
    assert job.result_summary["stage"] == "completed"
    assert job.result_summary["pct"] == 100


def test_recovery_fails_stale_job_when_source_missing(db):
    from app.services.ingestion_job_recovery import recover_orphaned_ingestion_jobs

    job = _make_job(db, str(ULID()), status="pending")

    stats = recover_orphaned_ingestion_jobs(db, limit=5, resume=False)

    db.refresh(job)
    assert stats["checked"] == 1
    assert stats["failed"] == 1
    assert job.status == "failed"
    assert job.error_message == "Recovery failed: related RawSource not found"
    assert job.result_summary["stage"] == "recovery_failed"


def test_cancel_ingestion_job_marks_job_cancelled_and_source_failed(db):
    from app.services.ingestion_job_recovery import cancel_ingestion_job

    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")

    result = cancel_ingestion_job(db, job.id, reason="演示中止")

    db.refresh(source)
    db.refresh(job)
    assert result["success"] is True
    assert result["status"] == "cancelled"
    assert job.status == "cancelled"
    assert job.error_message == "Cancelled: 演示中止"
    assert job.result_summary["stage"] == "cancelled"
    assert job.result_summary["pct"] == 100
    assert source.status == "failed"
    assert source.processing_error == "Cancelled: 演示中止"


def test_cancel_ingestion_job_is_idempotent_for_cancelled_job(db):
    from app.services.ingestion_job_recovery import cancel_ingestion_job

    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")

    first = cancel_ingestion_job(db, job.id, reason="first")
    second = cancel_ingestion_job(db, job.id, reason="second")

    assert first["success"] is True
    assert second["success"] is True
    assert second["already_cancelled"] is True


def test_cancel_ingestion_job_rejects_completed_job(db):
    from app.services.ingestion_job_recovery import cancel_ingestion_job

    source = _make_source(db, status="completed")
    job = _make_job(db, source.id, status="completed")

    result = cancel_ingestion_job(db, job.id, reason="too late")

    assert result["success"] is False
    assert result["status_code"] == 400
    assert "cannot cancel" in result["error"]


def test_cancel_ingestion_job_api(client, db):
    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")

    resp = client.post(f"/api/v1/ingestion/jobs/{job.id}/cancel", json={"reason": "用户切换资料"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "cancelled"
    db.refresh(job)
    assert job.status == "cancelled"

def test_progress_active_includes_persisted_pending_ingestion_job(client, db):
    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="pending")

    resp = client.get("/api/v1/progress/active")

    assert resp.status_code == 200
    rows = resp.json()
    match = next((item for item in rows if item.get("job_id") == job.id), None)
    assert match is not None
    assert match["status"] == "pending"
    assert match["source"] == "job_run"
    assert match["workflow"] == "ingestion"
    assert len(match["stages"]) >= 5
    assert match["current_stage"] == "parse"
    assert match["current_stage_label"] == "解析内容"


def test_progress_detail_restores_ingestion_stage_list_from_job_run(client, db):
    source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")

    resp = client.get(f"/api/v1/progress/{job.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job.id
    assert data["source"] == "job_run"
    assert data["workflow"] == "ingestion"
    assert data["current_stage"] == "parse"
    assert data["current_stage_label"] == "解析内容"
    assert data["progress_pct"] == 20
    assert len(data["stages"]) >= 5
    assert data["stages"][0]["key"] == "load"
    assert data["stages"][0]["status"] == "completed"
    assert data["stages"][1]["key"] == "parse"
    assert data["stages"][1]["status"] == "running"


def test_ingestion_dispatcher_status_reports_runtime_and_persisted_jobs(client, db):
    source = _make_source(db, status="pending")
    job = _make_job(db, source.id, status="running")

    response = client.get("/api/v1/ingestion/dispatcher/status")

    assert response.status_code == 200
    data = response.json()
    assert data["dispatcher"] == "bounded_thread_pool"
    assert data["max_workers"] >= 1
    assert isinstance(data["active_or_queued_count"], int)
    assert isinstance(data["job_ids"], list)
    assert data["persisted_active_count"] == 1
    assert data["persisted_active_job_ids"] == [job.id]
    assert data["persisted_active_jobs"][0]["job_id"] == job.id
    assert data["persisted_active_jobs"][0]["source_id"] == source.id
    assert data["persisted_active_jobs"][0]["status"] == "running"


def test_ingestion_dispatcher_status_includes_lease_metadata(client, db):
    now = datetime.now(timezone.utc)
    source = _make_source(db, status="processing")
    job = _make_job(
        db,
        source.id,
        status="running",
        result_summary={
            "stage": "extraction",
            "pct": 40,
            "message": "extracting",
            "lease_state": "running",
            "lease_worker_id": "worker-visible-1",
            "lease_heartbeat_at": now.isoformat(),
            "lease_expires_at": (now + timedelta(seconds=60)).isoformat(),
            "lease_seconds": 60,
        },
    )

    response = client.get("/api/v1/ingestion/dispatcher/status")

    assert response.status_code == 200
    row = next(item for item in response.json()["persisted_active_jobs"] if item["job_id"] == job.id)
    assert row["lease_state"] == "running"
    assert row["lease_worker_id"] == "worker-visible-1"
    assert row["lease_heartbeat_at"] == job.result_summary["lease_heartbeat_at"]
    assert row["lease_expires_at"] == job.result_summary["lease_expires_at"]
    assert row["lease_seconds"] == 60
    assert row["lease_expired"] is False


def test_ingestion_dispatcher_records_lease_heartbeat_metadata(db):
    from app.services.ingestion_dispatcher import record_ingestion_job_heartbeat

    source = _make_source(db, status="pending")
    job = _make_job(db, source.id, status="pending")

    metadata = record_ingestion_job_heartbeat(
        db,
        job.id,
        source_id=source.id,
        state="submitted",
        worker_id="worker-test-1",
        lease_seconds=60,
    )

    db.refresh(job)
    assert metadata["lease_state"] == "submitted"
    assert metadata["lease_worker_id"] == "worker-test-1"
    assert metadata["lease_seconds"] == 60
    assert metadata["lease_heartbeat_at"]
    assert metadata["lease_expires_at"]
    assert job.result_summary["lease_state"] == "submitted"
    assert job.result_summary["lease_worker_id"] == "worker-test-1"
    assert job.result_summary["lease_heartbeat_at"] == metadata["lease_heartbeat_at"]
    assert job.result_summary["lease_expires_at"] == metadata["lease_expires_at"]


def test_reap_stale_ingestion_jobs_requeues_expired_lease(db, monkeypatch):
    from app.services.ingestion_job_recovery import reap_stale_ingestion_jobs

    now = datetime.now(timezone.utc)
    source = _make_source(db, status="processing")
    job = _make_job(
        db,
        source.id,
        status="running",
        result_summary={
            "stage": "extraction",
            "pct": 40,
            "message": "stale lease",
            "lease_state": "running",
            "lease_worker_id": "worker-lost-1",
            "lease_heartbeat_at": (now - timedelta(minutes=20)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
            "lease_seconds": 60,
        },
    )
    submitted: list[tuple[str, str]] = []

    def fake_submit(source_id: str, job_id: str):
        submitted.append((source_id, job_id))
        return {"submitted": True, "source_id": source_id, "job_id": job_id, "dispatcher": "bounded_thread_pool"}

    monkeypatch.setattr("app.services.ingestion_job_recovery.submit_ingestion_job", fake_submit, raising=False)

    stats = reap_stale_ingestion_jobs(db, limit=5, resume=True, max_retries=3, now=now)

    db.refresh(source)
    db.refresh(job)
    assert stats["checked"] == 1
    assert stats["stale"] == 1
    assert stats["resumed"] == 1
    assert submitted == [(source.id, job.id)]
    assert source.status == "pending"
    assert job.status == "pending"
    assert job.retry_count == 1
    assert job.result_summary["stage"] == "stale_recovered"
    assert job.result_summary["previous_lease_state"] == "running"
    assert job.result_summary["previous_lease_worker_id"] == "worker-lost-1"


def test_reap_stale_ingestion_jobs_ignores_unexpired_lease(db, monkeypatch):
    from app.services.ingestion_job_recovery import reap_stale_ingestion_jobs

    now = datetime.now(timezone.utc)
    source = _make_source(db, status="processing")
    job = _make_job(
        db,
        source.id,
        status="running",
        result_summary={
            "stage": "extraction",
            "pct": 40,
            "lease_state": "running",
            "lease_worker_id": "worker-alive-1",
            "lease_heartbeat_at": now.isoformat(),
            "lease_expires_at": (now + timedelta(minutes=5)).isoformat(),
            "lease_seconds": 300,
        },
    )
    submitted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.ingestion_job_recovery.submit_ingestion_job",
        lambda source_id, job_id: submitted.append((source_id, job_id)),
        raising=False,
    )

    stats = reap_stale_ingestion_jobs(db, limit=5, resume=True, max_retries=3, now=now)

    db.refresh(job)
    assert stats["checked"] == 1
    assert stats["stale"] == 0
    assert stats["resumed"] == 0
    assert submitted == []
    assert job.status == "running"
    assert job.retry_count == 0


def test_reap_stale_ingestion_jobs_api(client, db):
    now = datetime.now(timezone.utc)
    source = _make_source(db, status="processing")
    job = _make_job(
        db,
        source.id,
        status="running",
        result_summary={
            "stage": "extraction",
            "pct": 40,
            "lease_state": "running",
            "lease_worker_id": "worker-lost-api",
            "lease_heartbeat_at": (now - timedelta(minutes=20)).isoformat(),
            "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
            "lease_seconds": 60,
        },
    )

    response = client.post("/api/v1/ingestion/dispatcher/reap-stale", params={"limit": 5, "resume": True, "max_retries": 3})

    assert response.status_code == 200
    data = response.json()
    assert data["checked"] == 1
    assert data["stale"] == 1
    assert data["resumed"] == 1
    assert data["jobs"][0]["job_id"] == job.id
    assert data["jobs"][0]["dispatch_status"]["dispatcher"] == "bounded_thread_pool"
    db.refresh(source)
    db.refresh(job)
    assert source.status == "pending"
    assert job.status == "pending"
    assert job.retry_count == 1


def test_progress_detail_includes_source_scope_and_rejects_mismatch(client, db):
    source = _make_source(db, status="processing")
    other_source = _make_source(db, status="processing")
    job = _make_job(db, source.id, status="running")

    compatible = client.get(f"/api/v1/progress/{job.id}")
    assert compatible.status_code == 200
    assert compatible.json()["source_id"] == source.id

    scoped = client.get(f"/api/v1/progress/{job.id}", params={"source_id": source.id})
    assert scoped.status_code == 200
    assert scoped.json()["source_id"] == source.id

    mismatch = client.get(f"/api/v1/progress/{job.id}", params={"source_id": other_source.id})
    assert mismatch.status_code == 403
    assert "source" in mismatch.json()["detail"].lower()


def test_progress_detail_restores_multi_source_scope_from_job_run(client, db):
    from app.schemas.job_run import JobRun

    now = datetime.now(timezone.utc)
    job = JobRun(
        id=str(ULID()),
        job_type="committee_debate",
        status="running",
        triggered_by="api",
        input_params={"source_ids": ["source-a", "source-b"]},
        result_summary={"stage": "context", "pct": 10},
        started_at=now,
        related_object_id="debate-1",
        related_object_type="debate_record",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()

    response = client.get(f"/api/v1/progress/{job.id}", params={"source_id": "source-b"})

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == job.id
    assert data["source_id"] == "source-a"
    assert data["source_ids"] == ["source-a", "source-b"]
    assert data["related_object_id"] == "debate-1"
    assert data["related_object_type"] == "debate_record"


@pytest.mark.asyncio
async def test_process_batch_async_returns_per_source_jobs(client, db):
    from fastapi import BackgroundTasks
    from app.api.v1.ingestion import process_batch
    from app.schemas.job_run import JobRun

    source_a = _make_source(db, status="pending")
    source_b = _make_source(db, status="pending")

    result = await process_batch(BackgroundTasks(), limit=2, async_mode=True, db=db)

    assert result["queued"] is True
    assert result["queued_count"] == 2
    assert result["source_ids"] == [source_a.id, source_b.id]
    assert len(result["job_ids"]) == 2
    assert len(result["jobs"]) == 2

    for item in result["jobs"]:
        job = db.get(JobRun, item["job_id"])
        assert job is not None
        assert job.related_object_id == item["source_id"]
        assert job.related_object_type == "raw_source"
        assert item["progress_url"] == f"/api/v1/progress/{item['job_id']}"
        progress_response = client.get(item["progress_url"])
        assert progress_response.status_code == 200
        assert progress_response.json()["source_id"] == item["source_id"]


@pytest.mark.asyncio
async def test_process_batch_async_uses_bounded_dispatcher_not_request_tasks(client, db, monkeypatch):
    from fastapi import BackgroundTasks
    from app.api.v1.ingestion import process_batch
    from app.schemas.job_run import JobRun

    source_a = _make_source(db, status="pending")
    source_b = _make_source(db, status="pending")
    background_tasks = BackgroundTasks()
    submitted: list[tuple[str, str]] = []
    job_counts_seen_at_submit: list[int] = []

    def fake_submit(source_id: str, job_id: str, *, reservation_id: str | None = None):
        assert reservation_id
        submitted.append((source_id, job_id))
        job_counts_seen_at_submit.append(
            db.query(JobRun)
            .filter(JobRun.job_type == "ingestion", JobRun.related_object_id.in_([source_a.id, source_b.id]))
            .count()
        )
        return {
            "submitted": True,
            "source_id": source_id,
            "job_id": job_id,
            "max_workers": 2,
        }

    monkeypatch.setattr("app.api.v1.ingestion.submit_ingestion_job", fake_submit, raising=False)

    result = await process_batch(background_tasks, limit=2, async_mode=True, db=db)

    assert len(background_tasks.tasks) == 0
    assert result["queued_count"] == 2
    assert submitted == [
        (source_a.id, result["job_ids"][0]),
        (source_b.id, result["job_ids"][1]),
    ]
    assert job_counts_seen_at_submit[0] == 2
    for item in result["jobs"]:
        assert item["dispatch_status"]["submitted"] is True
        assert item["dispatch_status"]["max_workers"] == 2


def test_process_selected_sources_api_queues_only_requested_sources(client, db):
    from app.schemas.job_run import JobRun

    source_a = _make_source(db, status="pending")
    source_b = _make_source(db, status="pending")
    source_c = _make_source(db, status="pending")

    response = client.post(
        "/api/v1/ingestion/process-selected",
        json={
            "source_ids": [source_a.id, source_b.id],
            "async_mode": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["queued"] is True
    assert data["queued_count"] == 2
    assert data["source_ids"] == [source_a.id, source_b.id]
    assert len(data["job_ids"]) == 2
    assert all(item["dispatch_status"]["dispatcher"] == "bounded_thread_pool" for item in data["jobs"])
    assert (
        db.query(JobRun)
        .filter(JobRun.job_type == "ingestion", JobRun.related_object_id == source_c.id)
        .count()
    ) == 0


@pytest.mark.asyncio
async def test_reprocess_source_returns_job_contract(client, db):
    from fastapi import BackgroundTasks
    from app.api.v1.knowledge import reprocess_source
    from app.schemas.job_run import JobRun

    source = _make_source(db, status="completed")
    source.raw_text = "需要重新解析的资料"
    db.commit()

    result = await reprocess_source(source.id, BackgroundTasks(), db=db)

    assert result["queued"] is True
    assert result["source_id"] == source.id
    assert result["job_id"]
    assert result["progress_url"] == f"/api/v1/progress/{result['job_id']}"
    job = db.get(JobRun, result["job_id"])
    assert job is not None
    assert job.related_object_id == source.id
    assert job.related_object_type == "raw_source"

    progress_response = client.get(result["progress_url"])
    assert progress_response.status_code == 200
    assert progress_response.json()["source_id"] == source.id


@pytest.mark.asyncio
async def test_scheduler_ingestion_stale_reaper_job_calls_reaper(monkeypatch):
    from app.config import settings
    from app.services import scheduler

    monkeypatch.setattr(settings, "INGESTION_RECOVERY_MAX_JOBS", 7)
    monkeypatch.setattr(settings, "INGESTION_RECOVERY_MAX_RETRIES", 2)

    closed_sessions: list[bool] = []

    class DummySession:
        def close(self):
            closed_sessions.append(True)

    calls: dict[str, object] = {}

    def fake_session_local():
        return DummySession()

    def fake_reap_stale_ingestion_jobs(db, *, limit, resume, max_retries):
        calls["db"] = db
        calls["limit"] = limit
        calls["resume"] = resume
        calls["max_retries"] = max_retries
        return {
            "checked": 1,
            "stale": 1,
            "recoverable": 1,
            "resumed": 1,
            "closed": 0,
            "failed": 0,
            "dead_lettered": 0,
            "jobs": [{"job_id": "job-1", "action": "resumed"}],
        }

    monkeypatch.setattr("app.database.SessionLocal", fake_session_local)
    monkeypatch.setattr("app.services.ingestion_job_recovery.reap_stale_ingestion_jobs", fake_reap_stale_ingestion_jobs)

    result = await scheduler.ingestion_stale_reaper_job.__wrapped__(triggered_by="test")

    assert result["triggered_by"] == "test"
    assert result["stale"] == 1
    assert result["resumed"] == 1
    assert calls["limit"] == 7
    assert calls["resume"] is True
    assert calls["max_retries"] == 2
    assert closed_sessions == [True]
