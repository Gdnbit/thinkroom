from datetime import datetime, timezone

from ulid import ULID

from app.schemas.briefing_card import BriefingCard
from app.schemas.decision_snapshot import DecisionSnapshot
from app.services.decision_snapshot_service import create_briefing_decision_snapshot


def _briefing() -> BriefingCard:
    now = datetime.now(timezone.utc)
    return BriefingCard(
        id=str(ULID()),
        title="service idempotency briefing",
        topic="idempotency",
        question="Should repeated snapshot creation be idempotent?",
        direction="neutral",
        setup_quality="medium",
        time_horizon="short",
        asset_scope=["BTC"],
        executive_summary="service idempotency briefing",
        supporting_points=[],
        contrary_points=[],
        key_risks=[],
        strategy_fit_summary=None,
        recommended_action="watch",
        invalidation_conditions=[],
        source_claim_ids=[],
        source_event_ids=[],
        model_used="test-model",
        committee_level="single",
        status="active",
        generated_at=now,
        created_at=now,
        updated_at=now,
    )


def test_create_briefing_decision_snapshot_is_idempotent_for_same_briefing(db):
    briefing = _briefing()
    db.add(briefing)
    db.flush()

    first = create_briefing_decision_snapshot(
        db,
        briefing=briefing,
        question=briefing.question or "",
        asset="BTC",
        claims=[{"id": "claim-1", "summary": "BTC neutral"}],
        events=[],
        market_state={"price": 100.0},
        flow_signals=[],
        data_coverage={"coverage": "test"},
        decision_guardrail={"effective_action": "watch"},
        source_mode="test",
        source_ids=["source-1"],
    )
    second = create_briefing_decision_snapshot(
        db,
        briefing=briefing,
        question=briefing.question or "",
        asset="BTC",
        claims=[{"id": "claim-1", "summary": "BTC neutral"}],
        events=[],
        market_state={"price": 100.0},
        flow_signals=[],
        data_coverage={"coverage": "test"},
        decision_guardrail={"effective_action": "watch"},
        source_mode="test",
        source_ids=["source-1"],
    )

    assert first.id == second.id
    assert db.query(DecisionSnapshot).count() == 1
