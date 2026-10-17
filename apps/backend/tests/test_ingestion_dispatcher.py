from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from ulid import ULID


class _HoldingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))
        return Future()


def test_threadpool_worker_count_serializes_sqlite_but_preserves_postgres_concurrency():
    from app.services.ingestion_dispatcher import _effective_threadpool_worker_count

    assert _effective_threadpool_worker_count(4, "sqlite:///./dev.db") == 1
    assert _effective_threadpool_worker_count(4, "sqlite+pysqlite:///./dev.db") == 1
    assert _effective_threadpool_worker_count(4, "postgresql://db/agent") == 4
    assert _effective_threadpool_worker_count(0, "postgresql://db/agent") == 1


@pytest.fixture
def isolated_dispatcher(monkeypatch):
    from app.config import settings
    from app.services import ingestion_dispatcher

    executor = _HoldingExecutor()
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(ingestion_dispatcher, "_MAX_WORKERS", 1)
    monkeypatch.setattr(ingestion_dispatcher, "_MAX_OUTSTANDING", 2, raising=False)
    monkeypatch.setattr(ingestion_dispatcher, "_RESERVATION_TTL_SECONDS", 30, raising=False)
    monkeypatch.setattr(ingestion_dispatcher, "_executor", executor)
    monkeypatch.setattr(ingestion_dispatcher, "_inflight", {})
    monkeypatch.setattr(ingestion_dispatcher, "_admitted_job_ids", set(), raising=False)
    monkeypatch.setattr(ingestion_dispatcher, "_reservations", {}, raising=False)
    monkeypatch.setattr(
        ingestion_dispatcher,
        "_record_ingestion_job_heartbeat_with_session",
        lambda *_args, **_kwargs: None,
    )
    return ingestion_dispatcher, executor


def test_local_dispatcher_rejects_work_beyond_outstanding_capacity(isolated_dispatcher):
    dispatcher, executor = isolated_dispatcher

    first = dispatcher.submit_ingestion_job("source-1", "job-1")
    second = dispatcher.submit_ingestion_job("source-2", "job-2")
    rejected = dispatcher.submit_ingestion_job("source-3", "job-3")
    status = dispatcher.list_inflight_ingestion_jobs()

    assert first["submitted"] is True
    assert second["submitted"] is True
    assert rejected == {
        "submitted": False,
        "reason": "capacity_exceeded",
        "retryable": True,
        "source_id": "source-3",
        "job_id": "job-3",
        "dispatcher": "bounded_thread_pool",
        "mode": "threadpool",
        "durable": False,
        "max_workers": 1,
        "max_outstanding": 2,
        "active_or_queued_count": 2,
        "reserved_count": 0,
        "available_capacity": 0,
        "saturated": True,
    }
    assert len(executor.calls) == 2
    assert status["active_or_queued_count"] == 2
    assert status["available_capacity"] == 0
    assert status["saturated"] is True
    assert status["durable"] is False
    assert status["configured_max_workers"] >= status["max_workers"]
    assert status["database_dialect"] == "sqlite"
    assert status["write_concurrency_policy"] == "serialized_single_writer"


def test_local_capacity_reservation_is_all_or_none_and_protected(isolated_dispatcher):
    dispatcher, executor = isolated_dispatcher

    rejected = dispatcher.reserve_ingestion_capacity(3)
    reservation = dispatcher.reserve_ingestion_capacity(2)
    unreserved = dispatcher.submit_ingestion_job("source-other", "job-other")
    first = dispatcher.submit_ingestion_job(
        "source-1",
        "job-1",
        reservation_id=reservation["reservation_id"],
    )
    second = dispatcher.submit_ingestion_job(
        "source-2",
        "job-2",
        reservation_id=reservation["reservation_id"],
    )

    assert rejected["reserved"] is False
    assert rejected["reason"] == "capacity_exceeded"
    assert rejected["available_capacity"] == 2
    assert reservation["reserved"] is True
    assert reservation["reserved_count"] == 2
    assert unreserved["reason"] == "capacity_exceeded"
    assert first["submitted"] is True
    assert second["submitted"] is True
    assert len(executor.calls) == 2
    assert dispatcher.list_inflight_ingestion_jobs()["reserved_count"] == 0


def test_duplicate_job_submission_is_idempotent(isolated_dispatcher):
    dispatcher, executor = isolated_dispatcher

    first = dispatcher.submit_ingestion_job("source-1", "job-1")
    duplicate = dispatcher.submit_ingestion_job("source-1", "job-1")

    assert first["submitted"] is True
    assert duplicate["submitted"] is False
    assert duplicate["reason"] == "already_submitted"
    assert duplicate["idempotent"] is True
    assert len(executor.calls) == 1


def _make_source(db, *, status: str):
    from app.schemas.raw_source import RawSource

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="dispatcher fixture",
        raw_text="队列可靠性测试资料",
        language="zh",
        status=status,
        word_count=9,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _make_job(db, source_id: str, *, status: str, result_summary: dict):
    from app.schemas.job_run import JobRun

    now = datetime.now(timezone.utc)
    job = JobRun(
        id=str(ULID()),
        job_type="ingestion",
        status=status,
        triggered_by="api",
        input_params={"source_id": source_id},
        result_summary=result_summary,
        related_object_id=source_id,
        related_object_type="raw_source",
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_selected_batch_rejects_before_creating_any_job(client, db, monkeypatch):
    from app.schemas.job_run import JobRun

    source_a = _make_source(db, status="pending")
    source_b = _make_source(db, status="pending")
    monkeypatch.setattr(
        "app.api.v1.ingestion.reserve_ingestion_capacity",
        lambda count: {
            "reserved": False,
            "reason": "capacity_exceeded",
            "retryable": True,
            "requested_count": count,
            "available_capacity": 1,
            "max_outstanding": 2,
        },
        raising=False,
    )

    response = client.post(
        "/api/v1/ingestion/process-selected",
        json={"source_ids": [source_a.id, source_b.id], "async_mode": True},
    )

    assert response.status_code == 429
    assert response.json()["detail"]["reason"] == "capacity_exceeded"
    assert db.query(JobRun).filter(JobRun.job_type == "ingestion").count() == 0


def test_completed_job_redelivery_returns_prior_result_without_rerun(db, monkeypatch):
    from app.workflows.ingestion_workflow import IngestionWorkflow

    source = _make_source(db, status="completed")
    prior_result = {
        "success": True,
        "source_id": source.id,
        "job_id": "will-be-replaced",
        "claims_extracted": 3,
    }
    job = _make_job(db, source.id, status="completed", result_summary=prior_result)
    prior_result["job_id"] = job.id
    job.result_summary = prior_result
    db.commit()
    workflow = IngestionWorkflow(db)
    monkeypatch.setattr(
        workflow,
        "_do_process",
        lambda *_args, **_kwargs: pytest.fail("completed job must not rerun"),
    )

    result = workflow.process_source(source.id, job_id=job.id)

    assert result["success"] is True
    assert result["job_id"] == job.id
    assert result["claims_extracted"] == 3
    assert result["idempotent_replay"] is True


def test_lease_finalization_normalizes_naive_database_timestamp_to_utc():
    from app.services.ingestion_dispatcher import finalize_ingestion_lease_metadata

    result = finalize_ingestion_lease_metadata(
        {"lease_state": "running", "lease_expires_at": "future"},
        state="completed",
        finalized_at=datetime(2026, 7, 12, 9, 30, 0),
    )

    assert result["lease_released_at"] == "2026-07-12T09:30:00+00:00"


@pytest.mark.parametrize(
    ("success", "expected_status", "expected_lease_state"),
    [
        (True, "completed", "completed"),
        (False, "failed", "failed"),
    ],
)
def test_process_source_finalizes_active_lease(db, monkeypatch, success, expected_status, expected_lease_state):
    from app.workflows.ingestion_workflow import IngestionWorkflow

    worker_id = "celery:worker:test-task"
    source = _make_source(db, status="pending")
    job = _make_job(
        db,
        source.id,
        status="running",
        result_summary={
            "lease_state": "running",
            "lease_worker_id": worker_id,
            "lease_acquired_at": datetime.now(timezone.utc).isoformat(),
            "lease_expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        },
    )
    workflow = IngestionWorkflow(db)
    monkeypatch.setattr(
        workflow,
        "_do_process",
        lambda *_args, **_kwargs: {
            "success": success,
            "source_id": source.id,
            **({} if success else {"error": "controlled failure"}),
        },
    )

    workflow.process_source(source.id, job_id=job.id, lease_owner_id=worker_id)

    db.expire_all()
    persisted = db.get(type(job), job.id)
    assert persisted.status == expected_status
    assert persisted.result_summary["lease_state"] == expected_lease_state
    assert persisted.result_summary["lease_worker_id"] == worker_id
    assert persisted.result_summary["lease_expires_at"] is None
    assert persisted.result_summary["lease_released_at"]


def test_active_unexpired_lease_prevents_concurrent_duplicate_execution(db, monkeypatch):
    from app.workflows.ingestion_workflow import IngestionWorkflow

    source = _make_source(db, status="processing")
    job = _make_job(
        db,
        source.id,
        status="running",
        result_summary={
            "stage": "parse",
            "pct": 20,
            "lease_state": "running",
            "lease_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        },
    )
    workflow = IngestionWorkflow(db)
    monkeypatch.setattr(
        workflow,
        "_do_process",
        lambda *_args, **_kwargs: pytest.fail("active leased job must not run concurrently"),
    )

    result = workflow.process_source(source.id, job_id=job.id)

    assert result["success"] is False
    assert result["duplicate_active"] is True
    assert result["retryable"] is True
    assert result["job_id"] == job.id


def test_celery_application_uses_long_task_reliability_contract():
    from app.celery_app import celery_app

    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_ignore_result is True
    assert celery_app.conf.task_default_queue == "ingestion"
    assert celery_app.conf.task_routes["app.tasks.ingestion.process_ingestion_job"]["queue"] == "ingestion"


def test_celery_ingestion_task_passes_identifiers_only(monkeypatch):
    from app.tasks import ingestion

    captured = []
    monkeypatch.setattr(
        ingestion,
        "run_ingestion_job",
        lambda source_id, job_id, worker_id=None: captured.append((source_id, job_id, worker_id))
        or {"executed": True},
    )

    result = ingestion.process_ingestion_job.run("source-safe", "job-safe")

    assert result == {"executed": True}
    assert len(captured) == 1
    assert captured[0][0:2] == ("source-safe", "job-safe")
    assert captured[0][2]


def test_celery_eager_delivery_executes_identifier_task(monkeypatch):
    from app.celery_app import celery_app
    from app.tasks import ingestion

    captured = []
    monkeypatch.setattr(
        ingestion,
        "run_ingestion_job",
        lambda source_id, job_id, worker_id=None: captured.append((source_id, job_id, worker_id))
        or {"executed": True, "job_id": job_id},
    )
    previous_eager = celery_app.conf.task_always_eager
    previous_propagates = celery_app.conf.task_eager_propagates
    try:
        celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
        result = ingestion.process_ingestion_job.apply(
            args=["source-eager", "job-eager"],
            task_id="job-eager",
        )
    finally:
        celery_app.conf.update(
            task_always_eager=previous_eager,
            task_eager_propagates=previous_propagates,
        )

    assert result.successful()
    assert result.result == {"executed": True, "job_id": "job-eager"}
    assert captured[0][0:2] == ("source-eager", "job-eager")


def test_celery_dispatch_receipt_is_durable_and_identifier_only(monkeypatch):
    from app.config import settings
    from app.services import ingestion_dispatcher as dispatcher

    calls = []
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "INGESTION_DISPATCHER_MODE", "celery")
    monkeypatch.setattr(dispatcher, "_admitted_job_ids", set())
    monkeypatch.setattr(dispatcher, "_inflight", {})
    monkeypatch.setattr(dispatcher, "_reservations", {})
    monkeypatch.setattr(
        dispatcher,
        "_submit_celery_job",
        lambda source_id, job_id: calls.append((source_id, job_id)) or {"task_id": job_id},
        raising=False,
    )

    receipt = dispatcher.submit_ingestion_job("source-safe", "job-safe")

    assert receipt["submitted"] is True
    assert receipt["dispatcher"] == "celery"
    assert receipt["mode"] == "celery"
    assert receipt["durable"] is True
    assert calls == [("source-safe", "job-safe")]
    assert "document" not in str(receipt).lower()
    assert "prompt" not in str(receipt).lower()


def test_celery_broker_failure_returns_safe_retryable_receipt(monkeypatch):
    from app.config import settings
    from app.services import ingestion_dispatcher as dispatcher

    persisted_failures = []
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "INGESTION_DISPATCHER_MODE", "celery")
    monkeypatch.setattr(dispatcher, "_admitted_job_ids", set())
    monkeypatch.setattr(dispatcher, "_inflight", {})
    monkeypatch.setattr(dispatcher, "_reservations", {})

    def fail_broker(_source_id, _job_id):
        raise RuntimeError("redis://secret-user:secret-password@broker/internal")

    monkeypatch.setattr(dispatcher, "_submit_celery_job", fail_broker, raising=False)
    monkeypatch.setattr(
        dispatcher,
        "_mark_dispatch_failure",
        lambda source_id, job_id, exc: persisted_failures.append((source_id, job_id, type(exc).__name__)),
    )

    receipt = dispatcher.submit_ingestion_job("source-safe", "job-safe")

    assert receipt["submitted"] is False
    assert receipt["reason"] == "dispatch_failed"
    assert receipt["retryable"] is True
    assert receipt["error_type"] == "RuntimeError"
    assert "secret-password" not in str(receipt)
    assert persisted_failures == [("source-safe", "job-safe", "RuntimeError")]


def test_job_execution_claim_is_single_owner_and_completed_redelivery_is_idempotent(db):
    from app.services.ingestion_dispatcher import claim_ingestion_job_execution

    source = _make_source(db, status="pending")
    job = _make_job(db, source.id, status="pending", result_summary={"stage": "queued"})

    first = claim_ingestion_job_execution(
        db,
        job.id,
        source.id,
        worker_id="worker:first",
        lease_seconds=60,
    )
    duplicate = claim_ingestion_job_execution(
        db,
        job.id,
        source.id,
        worker_id="worker:second",
        lease_seconds=60,
    )
    job.status = "completed"
    db.commit()
    completed = claim_ingestion_job_execution(
        db,
        job.id,
        source.id,
        worker_id="worker:redelivery",
        lease_seconds=60,
    )

    assert first["execute"] is True
    assert duplicate["execute"] is False
    assert duplicate["reason"] == "active_lease"
    assert 1 <= duplicate["retry_after_seconds"] <= 62
    assert completed["execute"] is False
    assert completed["reason"] == "already_completed"
    assert completed["idempotent"] is True
    db.refresh(job)
    assert job.result_summary["lease_state"] == "completed"
    assert job.result_summary["lease_expires_at"] is None
    assert job.result_summary["lease_released_at"]


def test_celery_active_lease_requests_one_bounded_delayed_retry(monkeypatch):
    from app.tasks import ingestion

    captured = {}
    monkeypatch.setattr(
        ingestion,
        "run_ingestion_job",
        lambda *_args, **_kwargs: {
            "execute": False,
            "reason": "active_lease",
            "retryable": True,
            "retry_after_seconds": 17,
        },
    )

    def fake_retry(*, countdown, max_retries):
        captured.update(countdown=countdown, max_retries=max_retries)
        raise RuntimeError("retry requested")

    monkeypatch.setattr(ingestion.process_ingestion_job, "retry", fake_retry)

    with pytest.raises(RuntimeError, match="retry requested"):
        ingestion.process_ingestion_job.run("source-safe", "job-safe")

    assert captured == {"countdown": 19, "max_retries": 1}


def test_pending_job_with_completed_source_closes_without_rerun(db):
    from app.services.ingestion_dispatcher import claim_ingestion_job_execution

    source = _make_source(db, status="completed")
    job = _make_job(db, source.id, status="pending", result_summary={"stage": "persist"})

    claim = claim_ingestion_job_execution(
        db,
        job.id,
        source.id,
        worker_id="worker:redelivery",
        lease_seconds=60,
    )

    db.refresh(job)
    assert claim["execute"] is False
    assert claim["reason"] == "already_completed_source"
    assert claim["idempotent"] is True
    assert job.status == "completed"
    assert job.result_summary["stage"] == "completed"


def test_production_compose_defines_dedicated_ingestion_worker():
    yaml = pytest.importorskip("yaml")
    project_root = Path(__file__).resolve().parents[3]
    compose = yaml.safe_load((project_root / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    worker = compose["services"]["ingestion_worker"]
    command = " ".join(worker["command"] if isinstance(worker["command"], list) else [worker["command"]])
    assert "app.celery_app:celery_app" in command
    assert "--queues=ingestion" in command
    assert worker["depends_on"]["redis"]["condition"] == "service_healthy"
    assert worker["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert worker["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert worker["healthcheck"]
    assert compose["services"]["backend"]["depends_on"]["ingestion_worker"]["condition"] == "service_healthy"


def test_production_readiness_blocks_when_celery_worker_is_unreachable(db, monkeypatch):
    from app.config import settings
    from app.services import release_readiness

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(
        release_readiness,
        "list_inflight_ingestion_jobs",
        lambda: {
            "dispatcher": "celery",
            "mode": "celery",
            "durable": True,
            "max_workers": 2,
            "max_outstanding": 100,
            "active_or_queued_count": 0,
            "reserved_count": 0,
            "available_capacity": 100,
            "saturated": False,
            "worker_reachable": False,
            "worker_count": 0,
        },
    )

    check = release_readiness._ingestion_queue_check(db)

    assert check["status"] == "fail"
    assert check["signals"]["mode"] == "celery"
    assert check["signals"]["durable"] is True
    assert check["signals"]["worker_reachable"] is False


def test_local_readiness_allows_bounded_thread_pool_without_worker(db, monkeypatch):
    from app.config import settings
    from app.services import release_readiness

    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(
        release_readiness,
        "list_inflight_ingestion_jobs",
        lambda: {
            "dispatcher": "bounded_thread_pool",
            "mode": "threadpool",
            "durable": False,
            "max_workers": 2,
            "max_outstanding": 50,
            "active_or_queued_count": 0,
            "reserved_count": 0,
            "available_capacity": 50,
            "saturated": False,
            "worker_reachable": None,
            "worker_count": 0,
        },
    )

    check = release_readiness._ingestion_queue_check(db)

    assert check["status"] == "pass"
    assert check["signals"]["mode"] == "threadpool"


def test_reprocess_capacity_rejection_preserves_completed_source(client, db, monkeypatch):
    from fastapi import HTTPException

    source = _make_source(db, status="completed")
    original_text = source.raw_text

    def reject_capacity(_db, _count):
        raise HTTPException(
            status_code=429,
            detail={"reason": "capacity_exceeded", "retryable": True},
        )

    monkeypatch.setattr(
        "app.api.v1.ingestion._reserve_async_capacity_or_raise",
        reject_capacity,
    )

    response = client.post(f"/api/v1/knowledge/sources/{source.id}/reprocess")

    assert response.status_code == 429
    db.expire_all()
    assert source.status == "completed"
    assert source.raw_text == original_text
    assert source.word_count == 9
