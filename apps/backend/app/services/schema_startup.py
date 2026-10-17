from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine
from sqlalchemy.schema import MetaData


class SchemaSettings(Protocol):
    APP_ENV: str


@dataclass(frozen=True)
class SchemaStartupResult:
    mode: str
    expected_heads: tuple[str, ...]
    current_heads: tuple[str, ...]


class SchemaRevisionMismatch(RuntimeError):
    """Raised when a production database is not at the repository migration head."""


class SchemaRevisionConfigurationError(RuntimeError):
    """Raised when the repository Alembic migration state cannot be verified."""


def is_production_environment(settings: SchemaSettings) -> bool:
    return (settings.APP_ENV or "").strip().lower() in {"production", "prod"}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def repository_alembic_heads() -> tuple[str, ...]:
    backend_root = _backend_root()
    config_path = backend_root / "alembic.ini"
    script_path = backend_root / "alembic"
    if not config_path.is_file() or not script_path.is_dir():
        raise SchemaRevisionConfigurationError(
            "Alembic configuration is unavailable; production schema revision cannot be verified."
        )

    config = Config(str(config_path))
    config.set_main_option("script_location", str(script_path))
    heads = tuple(sorted({str(head) for head in ScriptDirectory.from_config(config).get_heads()}))
    if not heads:
        raise SchemaRevisionConfigurationError(
            "Repository Alembic head set is empty; production startup is not allowed."
        )
    return heads


def database_alembic_heads(engine: Engine) -> tuple[str, ...]:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return tuple(sorted({str(head) for head in context.get_current_heads()}))


def verify_database_schema_revision(engine: Engine) -> SchemaStartupResult:
    expected_heads = repository_alembic_heads()
    current_heads = database_alembic_heads(engine)
    if current_heads != expected_heads:
        raise SchemaRevisionMismatch(
            "Database schema revision mismatch: "
            f"expected_heads={list(expected_heads)}, current_heads={list(current_heads)}. "
            "Run `alembic upgrade head` as the deployment migration step before starting the backend."
        )
    return SchemaStartupResult(
        mode="verify",
        expected_heads=expected_heads,
        current_heads=current_heads,
    )


def prepare_database_schema(
    *,
    engine: Engine,
    metadata: MetaData,
    settings: SchemaSettings,
    legacy_migrator: Callable[[Engine], None],
) -> SchemaStartupResult:
    """Apply the environment-specific startup schema policy.

    Production is strictly read-only and accepts only an exact Alembic head match.
    Development and test retain the existing local bootstrap compatibility path.
    """

    if is_production_environment(settings):
        return verify_database_schema_revision(engine)

    metadata.create_all(bind=engine)
    legacy_migrator(engine)
    return SchemaStartupResult(mode="bootstrap", expected_heads=(), current_heads=())
