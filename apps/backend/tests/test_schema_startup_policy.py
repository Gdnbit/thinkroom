from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text

import app.services.schema_startup as schema_startup
import app.main as main_module
from app.services.schema_startup import (
    SchemaRevisionMismatch,
    prepare_database_schema,
    repository_alembic_heads,
    verify_database_schema_revision,
)


def _settings(app_env: str) -> SimpleNamespace:
    return SimpleNamespace(APP_ENV=app_env)


def test_production_startup_verifies_schema_without_mutation(monkeypatch):
    engine = object()
    metadata = Mock()
    legacy_migrator = Mock()
    verified = schema_startup.SchemaStartupResult(
        mode="verify",
        expected_heads=("0019_decision_trace_eval_reviews",),
        current_heads=("0019_decision_trace_eval_reviews",),
    )
    verifier = Mock(return_value=verified)
    monkeypatch.setattr(schema_startup, "verify_database_schema_revision", verifier)

    result = prepare_database_schema(
        engine=engine,
        metadata=metadata,
        settings=_settings("production"),
        legacy_migrator=legacy_migrator,
    )

    assert result == verified
    verifier.assert_called_once_with(engine)
    metadata.create_all.assert_not_called()
    legacy_migrator.assert_not_called()


def test_prod_alias_uses_the_same_read_only_policy(monkeypatch):
    verifier = Mock(return_value=schema_startup.SchemaStartupResult(
        mode="verify",
        expected_heads=("head",),
        current_heads=("head",),
    ))
    monkeypatch.setattr(schema_startup, "verify_database_schema_revision", verifier)
    metadata = Mock()
    legacy_migrator = Mock()
    engine = object()

    prepare_database_schema(
        engine=engine,
        metadata=metadata,
        settings=_settings("PrOd"),
        legacy_migrator=legacy_migrator,
    )

    verifier.assert_called_once_with(engine)
    metadata.create_all.assert_not_called()
    legacy_migrator.assert_not_called()


def test_development_startup_preserves_local_bootstrap(monkeypatch):
    verifier = Mock()
    monkeypatch.setattr(schema_startup, "verify_database_schema_revision", verifier)
    engine = object()
    metadata = Mock()
    legacy_migrator = Mock()

    result = prepare_database_schema(
        engine=engine,
        metadata=metadata,
        settings=_settings("development"),
        legacy_migrator=legacy_migrator,
    )

    assert result.mode == "bootstrap"
    assert result.expected_heads == ()
    assert result.current_heads == ()
    metadata.create_all.assert_called_once_with(bind=engine)
    legacy_migrator.assert_called_once_with(engine)
    verifier.assert_not_called()


def test_missing_production_revision_is_rejected_without_leaking_database_url(tmp_path):
    db_path = tmp_path / "unsafe-password-must-not-leak.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with pytest.raises(SchemaRevisionMismatch) as exc_info:
            verify_database_schema_revision(engine)

        message = str(exc_info.value)
        assert "expected_heads=" in message
        assert "current_heads=[]" in message
        assert "alembic upgrade head" in message
        assert "sqlite://" not in message
        assert "unsafe-password-must-not-leak" not in message
    finally:
        engine.dispose()


def test_matching_production_revision_is_accepted(tmp_path):
    db_path = tmp_path / "schema-at-head.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    expected_heads = repository_alembic_heads()
    assert expected_heads
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            ))
            for head in expected_heads:
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                    {"head": head},
                )

        result = verify_database_schema_revision(engine)

        assert result.mode == "verify"
        assert result.expected_heads == expected_heads
        assert result.current_heads == expected_heads
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_fastapi_production_startup_does_not_downgrade_schema_failure_to_warning(
    monkeypatch,
):
    failure = SchemaRevisionMismatch(
        "Database schema revision mismatch: expected_heads=['head'], current_heads=[]. "
        "Run `alembic upgrade head` before starting the backend."
    )
    monkeypatch.setattr(main_module.settings, "APP_ENV", "production")
    monkeypatch.setattr(
        main_module,
        "prepare_database_schema",
        Mock(side_effect=failure),
    )

    with pytest.raises(SchemaRevisionMismatch, match="alembic upgrade head"):
        await main_module.startup_event()
