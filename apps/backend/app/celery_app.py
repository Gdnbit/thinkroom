"""Celery transport for production ingestion workers.

Business state remains in JobRun/RawSource. Celery carries identifiers only and
ignores task results so the broker cannot become a second source of truth.
"""
from __future__ import annotations

from celery import Celery
from kombu import Queue

from app.config import settings


def _broker_url() -> str:
    return str(settings.CELERY_BROKER_URL or settings.REDIS_URL).strip()


def _positive_int(value, default: int) -> int:
    try:
        return max(1, int(value or default))
    except Exception:
        return default


INGESTION_TASK_NAME = "app.tasks.ingestion.process_ingestion_job"
INGESTION_QUEUE = str(settings.CELERY_INGESTION_QUEUE or "ingestion").strip() or "ingestion"

celery_app = Celery(
    "research_os",
    broker=_broker_url(),
    include=["app.tasks.ingestion"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    result_backend=None,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=_positive_int(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1),
    worker_max_tasks_per_child=_positive_int(settings.CELERY_WORKER_MAX_TASKS_PER_CHILD, 50),
    worker_max_memory_per_child=_positive_int(settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB, 1_500_000),
    task_default_queue=INGESTION_QUEUE,
    task_queues=(Queue(INGESTION_QUEUE, durable=True),),
    task_routes={INGESTION_TASK_NAME: {"queue": INGESTION_QUEUE}},
    task_soft_time_limit=_positive_int(settings.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS, 3300),
    task_time_limit=_positive_int(settings.CELERY_TASK_TIME_LIMIT_SECONDS, 3600),
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 1,
    },
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": _positive_int(settings.CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS, 4200),
    },
    worker_cancel_long_running_tasks_on_connection_loss=True,
    enable_utc=True,
    timezone="UTC",
)


__all__ = ["INGESTION_QUEUE", "INGESTION_TASK_NAME", "celery_app"]
