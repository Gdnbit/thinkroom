from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from ulid import ULID

from app.schemas.decision_snapshot import DecisionSnapshot


def _snapshot(trigger_type: str = "briefing", trigger_id: str = "01TESTTRIGGER000000000001") -> DecisionSnapshot:
    now = datetime.now(timezone.utc)
    return DecisionSnapshot(
        id=str(ULID()),
        trigger_type=trigger_type,
        trigger_id=trigger_id,
        asset="GENERAL",
        question="constraint smoke test",
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
        synthesis_summary="constraint smoke test",
        replay_status="pending",
        misleading_evidence=[],
        key_missed_signals=[],
        created_at=now,
        updated_at=now,
    )


def test_decision_snapshot_trigger_object_is_unique(db):
    """One business trigger object must map to one replay snapshot."""

    db.add(_snapshot())
    db.commit()

    db.add(_snapshot())
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_decision_snapshot_allows_same_trigger_id_across_trigger_types(db):
    """A briefing id and chat message id may collide without being the same trigger object."""

    db.add(_snapshot(trigger_type="briefing"))
    db.add(_snapshot(trigger_type="chat"))
    db.commit()

    assert db.query(DecisionSnapshot).count() == 2
