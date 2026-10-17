from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from ulid import ULID

import app.schemas  # noqa: F401 - register ORM models
from app.database import Base
from app.main import _migrate_missing_columns
from app.schemas.decision_snapshot import DecisionSnapshot
from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink
from app.schemas.raw_source import RawSource


def _sqlite_indexes(engine, table_name: str) -> list[tuple]:
    with engine.connect() as conn:
        return list(conn.execute(text(f"PRAGMA index_list('{table_name}')")).fetchall())


def _snapshot(trigger_id: str) -> DecisionSnapshot:
    now = datetime.now(timezone.utc)
    return DecisionSnapshot(
        id=str(ULID()),
        trigger_type="briefing",
        trigger_id=trigger_id,
        asset="GENERAL",
        question="legacy duplicate",
        snapshot_at=now,
        claims_snapshot=[],
        claims_count=0,
        data_coverage={},
        market_snapshot={},
        flow_snapshot={},
        events_snapshot=[],
        macro_snapshot={},
        signal_scorecard={},
        activated_seats=[],
        evidence_budget={},
        final_direction="neutral",
        final_conviction=None,
        final_action="watch",
        synthesis_summary="legacy duplicate",
        replay_status="pending",
        misleading_evidence=[],
        key_missed_signals=[],
        created_at=now,
        updated_at=now,
    )


def test_startup_schema_migration_backfills_decision_snapshot_trigger_unique_index(tmp_path):
    db_path = tmp_path / "legacy_startup.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS uq_decision_snapshots_trigger"))

        assert not any(
            row[1] == "uq_decision_snapshots_trigger"
            for row in _sqlite_indexes(engine, "decision_snapshots")
        )

        _migrate_missing_columns(engine)

        assert any(
            row[1] == "uq_decision_snapshots_trigger" and row[2] == 1
            for row in _sqlite_indexes(engine, "decision_snapshots")
        )
    finally:
        engine.dispose()


def test_startup_schema_migration_backfills_decision_trace_query_indexes(tmp_path):
    db_path = tmp_path / "legacy_trace_indexes.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS idx_decision_trace_events_related_object_created"))
            conn.execute(text("DROP INDEX IF EXISTS idx_decision_trace_events_recent"))

        initial_indexes = {row[1] for row in _sqlite_indexes(engine, "decision_trace_events")}
        assert "idx_decision_trace_events_related_object_created" not in initial_indexes
        assert "idx_decision_trace_events_recent" not in initial_indexes

        _migrate_missing_columns(engine)

        migrated_indexes = {row[1] for row in _sqlite_indexes(engine, "decision_trace_events")}
        assert "idx_decision_trace_events_related_object_created" in migrated_indexes
        assert "idx_decision_trace_events_recent" in migrated_indexes
    finally:
        engine.dispose()


def test_startup_schema_migration_backfills_decision_trace_source_links(tmp_path):
    db_path = tmp_path / "legacy_trace_source_links.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    try:
        Base.metadata.create_all(engine)
        session = Session()
        now = datetime.now(timezone.utc)
        event_id = str(ULID())
        try:
            session.add(RawSource(
                id="source-a",
                source_type="note",
                title="Retained source",
                raw_text="Retained source content.",
                language="en",
                status="completed",
                word_count=24,
                created_at=now,
                updated_at=now,
            ))
            session.add(DecisionTraceEvent(
                id=event_id,
                related_object_type="chat_message",
                related_object_id="assistant-source-backfill",
                run_id="run-source-backfill",
                session_id=None,
                job_id=None,
                event_type="scope",
                step_name="source_scope",
                status="ok",
                source_ids=["source-a", "source-b"],
                payload={"event": "scope"},
                created_at=now,
                updated_at=now,
            ))
            session.commit()
            assert session.query(DecisionTraceSourceLink).count() == 0
        finally:
            session.close()

        _migrate_missing_columns(engine)

        session = Session()
        try:
            links = (
                session.query(DecisionTraceSourceLink)
                .filter(DecisionTraceSourceLink.trace_event_id == event_id)
                .order_by(DecisionTraceSourceLink.source_id)
                .all()
            )
            assert [(link.trace_event_id, link.source_id) for link in links] == [
                (event_id, "source-a"),
            ]
        finally:
            session.close()
    finally:
        engine.dispose()


def test_startup_schema_migration_skips_unique_index_when_legacy_duplicates_exist(tmp_path):
    db_path = tmp_path / "legacy_duplicates.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS uq_decision_snapshots_trigger"))
        session = Session()
        try:
            session.add(_snapshot("01DUPLICATETRIGGER0000001"))
            session.add(_snapshot("01DUPLICATETRIGGER0000001"))
            session.commit()
        finally:
            session.close()

        _migrate_missing_columns(engine)

        assert not any(
            row[1] == "uq_decision_snapshots_trigger"
            for row in _sqlite_indexes(engine, "decision_snapshots")
        )
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM decision_snapshots")).scalar_one()
        assert count == 2
    finally:
        engine.dispose()
