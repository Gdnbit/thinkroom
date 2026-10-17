"""Helpers for freezing decision-time evidence snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from ulid import ULID

from app.schemas.briefing_card import BriefingCard
from app.schemas.decision_snapshot import DecisionSnapshot


def find_briefing_decision_snapshot(db: Session, briefing_id: str) -> DecisionSnapshot | None:
    return (
        db.query(DecisionSnapshot)
        .filter(
            DecisionSnapshot.trigger_type == "briefing",
            DecisionSnapshot.trigger_id == briefing_id,
        )
        .first()
    )


def create_briefing_decision_snapshot(
    db: Session,
    *,
    briefing: BriefingCard,
    question: str,
    asset: str,
    claims: list[dict[str, Any]] | None,
    events: list[dict[str, Any]] | None,
    market_state: dict[str, Any] | None,
    flow_signals: list[dict[str, Any]] | None,
    data_coverage: dict[str, Any] | None,
    decision_guardrail: dict[str, Any] | None,
    source_mode: str,
    source_ids: list[str] | None,
) -> DecisionSnapshot:
    """Create the replay snapshot required for every generated briefing."""

    existing_snapshot = find_briefing_decision_snapshot(db, briefing.id)
    if existing_snapshot:
        return existing_snapshot

    claims_snapshot = list(claims or [])
    events_snapshot = list(events or [])
    flow_snapshot = {"signals": list(flow_signals or [])}
    snapshot_at = briefing.generated_at or datetime.now(timezone.utc)
    final_action = (decision_guardrail or {}).get("effective_action") or briefing.recommended_action

    snapshot = DecisionSnapshot(
        id=str(ULID()),
        trigger_type="briefing",
        trigger_id=briefing.id,
        asset=asset,
        question=question,
        snapshot_at=snapshot_at,
        claims_snapshot=claims_snapshot,
        claims_count=len(claims_snapshot),
        data_coverage={
            **(data_coverage or {}),
            "source_mode": source_mode,
            "source_ids": list(source_ids or []),
            "claims": len(claims_snapshot),
            "events": len(events_snapshot),
            "has_market": bool(market_state),
            "flow_signals": len(flow_signals or []),
        },
        market_snapshot=market_state or {},
        flow_snapshot=flow_snapshot,
        events_snapshot=events_snapshot,
        macro_snapshot={},
        signal_scorecard={
            "setup_quality": briefing.setup_quality,
            "decision_guardrail": decision_guardrail or {},
        },
        activated_seats=["briefing"],
        evidence_budget={},
        final_direction=briefing.direction,
        final_conviction=None,
        final_action=final_action,
        synthesis_summary=(briefing.executive_summary or "")[:500] or None,
        replay_status="pending",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(snapshot)
    db.flush()
    return snapshot
