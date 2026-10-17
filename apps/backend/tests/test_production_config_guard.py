from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ProductionConfigError, Settings, assert_production_safe, production_config_errors


def test_production_guard_ignores_development_defaults():
    cfg = Settings(APP_ENV="development")

    assert production_config_errors(cfg) == []
    assert_production_safe(cfg)


def test_production_guard_rejects_unsafe_defaults():
    cfg = Settings(APP_ENV="production")

    errors = production_config_errors(cfg)
    assert "APP_DEBUG must be false in production" in errors
    assert "APP_API_KEY must be set in production" in errors
    assert "DATABASE_URL must not use SQLite in production" in errors
    assert "DEPLOYMENT_CONFIG_ID must be set in production" in errors
    assert "OPENROUTER_API_KEY must be set in production" in errors
    assert "REDIS_ENABLED must be true in production when REQUIRE_REDIS_IN_PRODUCTION is true" in errors
    assert "RATE_LIMIT_STORAGE_URI must use shared storage such as Redis in production" in errors
    assert (
        "MINIO_SECURE must be true in production when object storage is required, "
        "unless ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION is explicitly true"
    ) in errors
    assert "MINIO_ENDPOINT must be set in production when REQUIRE_OBJECT_STORAGE_IN_PRODUCTION is true" not in errors
    with pytest.raises(ProductionConfigError):
        assert_production_safe(cfg)


def test_production_guard_accepts_explicit_safe_configuration():
    cfg = Settings(
        APP_ENV="production",
        DEPLOYMENT_CONFIG_ID="prod-20260708T080000Z",
        APP_DEBUG=False,
        DATABASE_URL="postgresql://agent_user:agent_pass@postgres:5432/agent_db",
        APP_API_KEY="app-key-prod-1234567890",
        APP_SECRET_KEY="prod-secret-key-that-is-longer-than-32-characters",
        OPENROUTER_API_KEY="sk-or-prod",
        OPENROUTER_MOCK=False,
        REDIS_ENABLED=True,
        RATE_LIMIT_STORAGE_URI="redis://localhost:6379/1",
        MINIO_ENDPOINT="minio-prod.internal:9000",
        MINIO_SECURE=True,
        MINIO_ACCESS_KEY="prod-access",
        MINIO_SECRET_KEY="prod-secret",
        MINIO_BUCKET="agent-files-prod",
    )

    assert production_config_errors(cfg) == []
    assert_production_safe(cfg)


def test_production_guard_rejects_process_local_ingestion_dispatcher():
    cfg = Settings(
        APP_ENV="production",
        INGESTION_DISPATCHER_MODE="threadpool",
    )

    assert (
        "INGESTION_DISPATCHER_MODE must resolve to celery in production"
        in production_config_errors(cfg)
    )


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        (
            {"CELERY_INGESTION_QUEUE": ""},
            "CELERY_INGESTION_QUEUE must be set in production",
        ),
        (
            {"CELERY_WORKER_CONCURRENCY": 0},
            "CELERY_WORKER_CONCURRENCY must be at least 1 in production",
        ),
        (
            {"CELERY_WORKER_PREFETCH_MULTIPLIER": 2},
            "CELERY_WORKER_PREFETCH_MULTIPLIER must be 1 for long-running ingestion tasks",
        ),
        (
            {"CELERY_TASK_SOFT_TIME_LIMIT_SECONDS": 3600},
            "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS must be lower than CELERY_TASK_TIME_LIMIT_SECONDS",
        ),
        (
            {"CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS": 3800},
            "CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS must exceed CELERY_TASK_TIME_LIMIT_SECONDS by at least 300 seconds",
        ),
        (
            {"INGESTION_ASYNC_MAX_OUTSTANDING": 1},
            "INGESTION_ASYNC_MAX_OUTSTANDING must be at least CELERY_WORKER_CONCURRENCY in production",
        ),
        (
            {"INGESTION_MAX_ACTIVE_JOBS": 1},
            "INGESTION_MAX_ACTIVE_JOBS must be at least CELERY_WORKER_CONCURRENCY in production",
        ),
    ],
)
def test_production_guard_rejects_unsafe_celery_delivery_configuration(overrides, expected_error):
    cfg = Settings(APP_ENV="production", **overrides)

    assert expected_error in production_config_errors(cfg)


def test_production_guard_requires_object_storage_endpoint_and_bucket_when_enabled():
    cfg = Settings(
        APP_ENV="production",
        DEPLOYMENT_CONFIG_ID="prod-20260708T080000Z",
        APP_DEBUG=False,
        APP_API_KEY="app-key-prod-1234567890",
        APP_SECRET_KEY="prod-secret-key-that-is-longer-than-32-characters",
        OPENROUTER_API_KEY="sk-or-prod",
        OPENROUTER_MOCK=False,
        REDIS_ENABLED=True,
        RATE_LIMIT_STORAGE_URI="redis://localhost:6379/1",
        MINIO_ENDPOINT="",
        MINIO_ACCESS_KEY="prod-access",
        MINIO_SECRET_KEY="prod-secret",
        MINIO_BUCKET="",
    )

    errors = production_config_errors(cfg)

    assert "MINIO_ENDPOINT must be set in production when REQUIRE_OBJECT_STORAGE_IN_PRODUCTION is true" in errors
    assert "MINIO_BUCKET must be set in production when REQUIRE_OBJECT_STORAGE_IN_PRODUCTION is true" in errors


def test_production_dockerfile_keeps_single_worker_until_progress_is_shared():
    project_root = Path(__file__).resolve().parents[3]
    dockerfile = project_root / "apps" / "backend" / "Dockerfile"

    text = dockerfile.read_text(encoding="utf-8")

    assert '"--workers", "1"' in text
    assert '"--workers", "2"' not in text
