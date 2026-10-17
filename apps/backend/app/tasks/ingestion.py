"""Identifier-only Celery task for durable document ingestion."""
from __future__ import annotations

from app.celery_app import INGESTION_TASK_NAME, celery_app
from app.services.ingestion_dispatcher import run_ingestion_job


@celery_app.task(
    bind=True,
    name=INGESTION_TASK_NAME,
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def process_ingestion_job(self, source_id: str, job_id: str) -> dict:
    source_id = str(source_id or "").strip()
    job_id = str(job_id or "").strip()
    if not source_id or not job_id:
        raise ValueError("source_id and job_id are required")
    hostname = str(getattr(self.request, "hostname", None) or "worker")
    task_id = str(getattr(self.request, "id", None) or job_id)
    worker_id = f"celery:{hostname}:{task_id}"
    result = run_ingestion_job(source_id, job_id, worker_id=worker_id)
    if result.get("reason") == "active_lease" and result.get("retryable"):
        retry_after_seconds = max(1, int(result.get("retry_after_seconds") or 1))
        raise self.retry(countdown=retry_after_seconds + 2, max_retries=1)
    return result


__all__ = ["process_ingestion_job"]
