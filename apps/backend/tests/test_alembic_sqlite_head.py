from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect as sa_inspect

import app.schemas  # noqa: F401 - populate Base.metadata for the drift assertion
from app.database import Base


def _backend_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "apps" / "backend"


def _alembic_env(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "APP_ENV": "test",
        "LOG_DIR": "",
        "SENTRY_DSN": "",
        "OPENROUTER_MOCK": "true",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


def _run_alembic(db_path: Path, *args: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_backend_dir()),
        env=_alembic_env(db_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def test_alembic_upgrade_head_sqlite_creates_required_indexes(tmp_path):
    """Fresh SQLite migrations must reach head and create required production indexes."""

    db_path = tmp_path / "alembic_head.db"

    upgrade = _run_alembic(db_path, "upgrade", "head")
    assert upgrade.returncode == 0, f"alembic upgrade head failed:\n{upgrade.stderr}\n{upgrade.stdout}"

    current = _run_alembic(db_path, "current", timeout=30)
    current_out = (current.stdout or "") + (current.stderr or "")
    assert current.returncode == 0, f"alembic current failed:\n{current_out}"
    assert "0020_job_run_scope_columns" in current_out

    with sqlite3.connect(db_path) as conn:
        snapshot_indexes = conn.execute("PRAGMA index_list('decision_snapshots')").fetchall()
        trace_indexes = conn.execute("PRAGMA index_list('decision_trace_events')").fetchall()
        trace_link_indexes = conn.execute("PRAGMA index_list('decision_trace_source_links')").fetchall()
        trace_review_indexes = conn.execute("PRAGMA index_list('decision_trace_eval_reviews')").fetchall()
        trace_link_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decision_trace_source_links'"
        ).fetchone()
        trace_review_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decision_trace_eval_reviews'"
        ).fetchone()

    unique_indexes = {
        row[1]
        for row in snapshot_indexes
        if row[1] == "uq_decision_snapshots_trigger" and row[2] == 1
    }
    assert unique_indexes == {"uq_decision_snapshots_trigger"}

    trace_index_names = {row[1] for row in trace_indexes}
    assert "idx_decision_trace_events_related_object_created" in trace_index_names
    assert "idx_decision_trace_events_recent" in trace_index_names
    assert trace_link_table is not None
    assert "idx_decision_trace_source_links_source" in {row[1] for row in trace_link_indexes}
    assert trace_review_table is not None
    trace_review_index_names = {row[1] for row in trace_review_indexes}
    assert "idx_decision_trace_eval_reviews_status_updated" in trace_review_index_names
    assert "ix_decision_trace_eval_reviews_candidate_id" in trace_review_index_names


def test_alembic_upgrade_head_sqlite_contains_every_orm_table_and_column(tmp_path):
    """Production migrations must not rely on the development auto-column compatibility path."""

    db_path = tmp_path / "alembic_orm_drift.db"

    upgrade = _run_alembic(db_path, "upgrade", "head")
    assert upgrade.returncode == 0, f"alembic upgrade head failed:\n{upgrade.stderr}\n{upgrade.stdout}"

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    inspector = sa_inspect(engine)
    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    try:
        for table_name, table in sorted(Base.metadata.tables.items()):
            if not inspector.has_table(table_name):
                missing_tables.append(table_name)
                continue
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            missing = sorted(column.name for column in table.columns if column.name not in actual_columns)
            if missing:
                missing_columns[table_name] = missing
    finally:
        engine.dispose()

    assert missing_tables == []
    assert missing_columns == {}


def test_job_run_scope_migration_accepts_columns_from_legacy_auto_repair(tmp_path):
    """Databases repaired by the old dev startup path must still advance to the new head."""

    db_path = tmp_path / "legacy_auto_repaired_job_runs.db"
    upgrade_to_0019 = _run_alembic(db_path, "upgrade", "0019_decision_trace_eval_reviews")
    assert upgrade_to_0019.returncode == 0, (
        "alembic upgrade to 0019 failed:\n"
        f"{upgrade_to_0019.stderr}\n{upgrade_to_0019.stdout}"
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE job_runs ADD COLUMN session_id VARCHAR(26)")
        conn.execute("ALTER TABLE job_runs ADD COLUMN source_ids JSON")
        conn.execute("ALTER TABLE job_runs ADD COLUMN object_scope JSON")
        conn.commit()

    upgrade_to_head = _run_alembic(db_path, "upgrade", "head")
    assert upgrade_to_head.returncode == 0, (
        "legacy auto-repaired database could not reach head:\n"
        f"{upgrade_to_head.stderr}\n{upgrade_to_head.stdout}"
    )

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info('job_runs')").fetchall()}
        revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert {"session_id", "source_ids", "object_scope"}.issubset(columns)
    assert revision == "0020_job_run_scope_columns"


def test_alembic_upgrade_head_sqlite_backfills_decision_trace_source_links(tmp_path):
    """Existing JSON source_ids must be materialized into source-link rows."""

    db_path = tmp_path / "trace_source_links.db"

    upgrade_to_0017 = _run_alembic(db_path, "upgrade", "0017_decision_trace_query_indexes")
    assert upgrade_to_0017.returncode == 0, (
        "alembic upgrade to 0017 failed:\n"
        f"{upgrade_to_0017.stderr}\n{upgrade_to_0017.stdout}"
    )

    now = "2026-07-03 00:00:00.000000"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO decision_trace_events (
                related_object_type, related_object_id, run_id, session_id, job_id,
                event_type, step_name, status, model, skill_role,
                duration_ms, cost_usd, source_ids, payload,
                id, created_at, updated_at
            )
            VALUES (
                'chat_message', 'assistant-source-backfill', 'run-source-backfill',
                NULL, NULL, 'scope', 'source_scope', 'ok', NULL, NULL,
                NULL, NULL, '["source-a", "source-b"]', '{}',
                '01KTRACESOURCEBACKFILL01', ?, ?
            )
            """,
            (now, now),
        )
        conn.commit()

    upgrade_to_head = _run_alembic(db_path, "upgrade", "head")
    assert upgrade_to_head.returncode == 0, (
        "alembic upgrade head failed:\n"
        f"{upgrade_to_head.stderr}\n{upgrade_to_head.stdout}"
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT trace_event_id, source_id
            FROM decision_trace_source_links
            ORDER BY source_id
            """
        ).fetchall()

    assert rows == [
        ("01KTRACESOURCEBACKFILL01", "source-a"),
        ("01KTRACESOURCEBACKFILL01", "source-b"),
    ]


def test_alembic_upgrade_head_sqlite_refuses_duplicate_decision_snapshots(tmp_path):
    """Existing duplicate trigger snapshots must be reconciled instead of overwritten."""

    db_path = tmp_path / "duplicate_snapshots.db"

    upgrade_to_0015 = _run_alembic(db_path, "upgrade", "0015_decision_trace_events")
    assert upgrade_to_0015.returncode == 0, (
        "alembic upgrade to 0015 failed:\n"
        f"{upgrade_to_0015.stderr}\n{upgrade_to_0015.stdout}"
    )

    now = "2026-07-03T00:00:00+00:00"
    rows = [
        ("01DUPSNAPSHOT000000000001", "briefing", "01DUPTRIGGER00000000001"),
        ("01DUPSNAPSHOT000000000002", "briefing", "01DUPTRIGGER00000000001"),
    ]
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO decision_snapshots (
                trigger_type, trigger_id, asset, question, snapshot_at,
                claims_snapshot, claims_count, data_coverage, market_snapshot,
                flow_snapshot, events_snapshot, macro_snapshot,
                signal_scorecard, activated_seats, evidence_budget,
                final_direction, final_conviction, final_action, synthesis_summary,
                outcome_direction, outcome_observed_at, outcome_pnl_pct,
                replay_notes, replay_status, misleading_evidence,
                key_missed_signals, id, created_at, updated_at
            )
            VALUES (
                ?, ?, ?, 'duplicate migration guard', ?,
                '[]', 0, '{}', '{}',
                '{}', '[]', '{}',
                '{}', '[]', '{}',
                'neutral', NULL, 'watch', 'duplicate migration guard',
                NULL, NULL, NULL,
                NULL, 'pending', '[]',
                '[]', ?, ?, ?
            )
            """,
            [
                (
                    trigger_type,
                    trigger_id,
                    "GENERAL",
                    now,
                    row_id,
                    now,
                    now,
                )
                for row_id, trigger_type, trigger_id in rows
            ],
        )
        conn.commit()

    upgrade_to_head = _run_alembic(db_path, "upgrade", "head")
    out = (upgrade_to_head.stdout or "") + (upgrade_to_head.stderr or "")
    assert upgrade_to_head.returncode != 0
    assert "Cannot add uq_decision_snapshots_trigger" in out
    assert "briefing:01DUPTRIGGER00000000001=2" in out
