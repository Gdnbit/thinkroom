"""Decision trace read API."""

from __future__ import annotations

from typing import Iterable, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink
from app.services.decision_trace_consistency import get_trace_source_link_consistency
from app.services.trace_eval_review_service import (
    prepare_trace_eval_promotion_draft,
    serialize_trace_eval_review,
    upsert_trace_eval_review,
)
from app.utils.source_scope import clean_source_ids
from app.utils.public_llm_trace import public_llm_trace
from app.verification.trace_eval_corpus import build_trace_eval_review_manifest, collect_trace_eval_cases

router = APIRouter()


class TraceEvalReviewRequest(BaseModel):
    review_status: str = Field(..., max_length=40)
    reviewer: str | None = Field(default=None, max_length=100)
    decision_note: str | None = Field(default=None, max_length=1000)
    promoted_case_id: str | None = Field(default=None, max_length=160)
    review_metadata: dict = Field(default_factory=dict)


class TraceEvalPromotionDraftRequest(BaseModel):
    reviewer_note: str = Field(..., min_length=1, max_length=1000)


def _serialize_event(event: DecisionTraceEvent) -> dict:
    payload = event.payload or {}
    if event.event_type == "llm_call":
        payload = public_llm_trace(payload)
    error_type = payload.get("error_type") if isinstance(payload, dict) else None
    return {
        "id": event.id,
        "related_object_type": event.related_object_type,
        "related_object_id": event.related_object_id,
        "run_id": event.run_id,
        "session_id": event.session_id,
        "job_id": event.job_id,
        "event_type": event.event_type,
        "step_name": event.step_name,
        "status": event.status,
        "model": event.model,
        "skill_role": event.skill_role,
        "error_type": error_type if isinstance(error_type, str) else None,
        "duration_ms": event.duration_ms,
        "cost_usd": event.cost_usd,
        "source_ids": event.source_ids or [],
        "payload": payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }


def _payload_strings(events: Iterable[DecisionTraceEvent], key: str) -> list[str]:
    values: list[str] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        raw = payload.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(item for item in raw if isinstance(item, str))
    return _unique_strings(values)


def _apply_source_id_filter(query, db: Session, source_id: str):
    cleaned = clean_source_ids([source_id])
    if not cleaned:
        return query.filter(False)
    source_id = cleaned[0]
    return query.join(
        DecisionTraceSourceLink,
        DecisionTraceSourceLink.trace_event_id == DecisionTraceEvent.id,
    ).filter(DecisionTraceSourceLink.source_id == source_id)


def _unique_strings(values: Iterable[object]) -> list[str]:
    return clean_source_ids([value for value in values if isinstance(value, str)])


def _coverage_status(*, expected_source_ids: list[str], missing_source_ids: list[str], event_count: int) -> str:
    if event_count <= 0:
        return "no_trace"
    if not expected_source_ids:
        return "no_expected_sources"
    if missing_source_ids:
        return "missing_sources"
    return "covered"


@router.get("/consistency")
def decision_trace_consistency(
    sample_limit: int = Query(20, ge=0, le=100),
    db: Session = Depends(get_db),
) -> dict:
    """Inspect source-link consistency without mutating persisted trace rows."""

    return get_trace_source_link_consistency(db, sample_limit=sample_limit)


@router.get("/eval-cases")
def decision_trace_eval_cases(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """List recent trace-derived eval cases without mutating persisted data."""

    items = collect_trace_eval_cases(db, limit=limit)
    return {
        "count": len(items),
        "items": items,
    }


@router.get("/eval-review-manifest")
def decision_trace_eval_review_manifest(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=5000),
    review_status: Optional[str] = Query(None, max_length=40),
    workflow: Optional[str] = Query(None, max_length=50),
    query: Optional[str] = Query(None, max_length=160),
    scan_limit: int = Query(1000, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Build a human-review manifest for trace-derived eval candidates."""

    return build_trace_eval_review_manifest(
        db,
        limit=limit,
        offset=offset,
        review_status=review_status,
        workflow=workflow,
        query=query,
        scan_limit=scan_limit,
    )


@router.post("/eval-reviews/{candidate_id}")
def decision_trace_eval_review_upsert(
    candidate_id: str = Path(..., max_length=160),
    request: TraceEvalReviewRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Persist human review state for a trace-derived eval candidate."""

    if request is None:
        raise HTTPException(status_code=400, detail="request body required")
    try:
        review = upsert_trace_eval_review(
            db,
            candidate_id=candidate_id,
            review_status=request.review_status,
            reviewer=request.reviewer,
            decision_note=request.decision_note,
            promoted_case_id=request.promoted_case_id,
            review_metadata=request.review_metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(review)
    return serialize_trace_eval_review(review)


@router.post("/eval-reviews/{candidate_id}/promotion-draft")
def decision_trace_eval_review_prepare_promotion_draft(
    candidate_id: str = Path(..., max_length=160),
    request: TraceEvalPromotionDraftRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Export replay fixture and implemented manifest draft for an approved trace candidate."""

    if request is None:
        raise HTTPException(status_code=400, detail="request body required")
    try:
        report = prepare_trace_eval_promotion_draft(
            db,
            candidate_id=candidate_id,
            reviewer_note=request.reviewer_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return report


@router.get("/trust-receipt")
def decision_trace_trust_receipt(
    related_object_type: str = Query(..., max_length=50),
    related_object_id: str = Query(..., max_length=64),
    expected_source_ids: Optional[list[str]] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """Summarize trace coverage for one decision-facing output.

    This endpoint is a canonical backend summary for UI trust receipts,
    exports, and audits. It does not mutate trace rows.
    """

    events = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == related_object_type,
            DecisionTraceEvent.related_object_id == related_object_id,
        )
        .order_by(DecisionTraceEvent.created_at.asc(), DecisionTraceEvent.id.asc())
        .limit(limit)
        .all()
    )

    event_types = _unique_strings(event.event_type for event in events)
    trace_source_ids = _unique_strings(
        source_id
        for event in events
        for source_id in (event.source_ids or [])
    )
    llm_events = [event for event in events if event.event_type == "llm_call"]
    llm_source_ids = _unique_strings(
        source_id
        for event in llm_events
        for source_id in (event.source_ids or [])
    )
    expected_ids = clean_source_ids(expected_source_ids)
    trace_source_set = set(trace_source_ids)
    covered_expected = [source_id for source_id in expected_ids if source_id in trace_source_set]
    missing_expected = [source_id for source_id in expected_ids if source_id not in trace_source_set]
    warning_events = [
        event for event in events
        if event.status in {"warning", "error"}
    ]
    models = _unique_strings(event.model for event in llm_events if event.model)
    requested_models = _payload_strings(llm_events, "requested_model")
    configured_fallback_models = _payload_strings(llm_events, "configured_fallback_models")
    total_duration_ms = sum(
        event.duration_ms or 0
        for event in events
        if event.duration_ms is not None
    )
    total_cost_usd = sum(
        event.cost_usd or 0.0
        for event in events
        if event.cost_usd is not None
    )

    return {
        "related_object_type": related_object_type,
        "related_object_id": related_object_id,
        "event_count": len(events),
        "event_types": event_types,
        "trace_source_ids": trace_source_ids,
        "expected_source_ids": expected_ids,
        "covered_expected_source_ids": covered_expected,
        "missing_expected_source_ids": missing_expected,
        "source_coverage_ratio": (
            len(covered_expected) / len(expected_ids)
            if expected_ids else None
        ),
        "coverage_status": _coverage_status(
            expected_source_ids=expected_ids,
            missing_source_ids=missing_expected,
            event_count=len(events),
        ),
        "has_llm_trace": bool(llm_events),
        "llm_call_count": len(llm_events),
        "llm_source_ids": llm_source_ids,
        "models": models,
        "requested_models": requested_models,
        "configured_fallback_models": configured_fallback_models,
        "warning_count": len(warning_events),
        "warning_event_types": _unique_strings(event.event_type for event in warning_events),
        "total_duration_ms": total_duration_ms or None,
        "total_cost_usd": total_cost_usd or None,
    }


@router.get("")
def list_decision_traces(
    related_object_type: Optional[str] = Query(None, max_length=50),
    related_object_id: Optional[str] = Query(None, max_length=64),
    event_type: Optional[str] = Query(None, max_length=50),
    source_id: Optional[str] = Query(None, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List persisted decision trace events.

    With a related object filter, events are returned in execution order.
    Without one, the endpoint behaves as a recent-events feed.
    """

    query = db.query(DecisionTraceEvent)
    scoped_to_object = bool(related_object_type or related_object_id)

    if related_object_type:
        query = query.filter(DecisionTraceEvent.related_object_type == related_object_type)
    if related_object_id:
        query = query.filter(DecisionTraceEvent.related_object_id == related_object_id)
    if event_type:
        query = query.filter(DecisionTraceEvent.event_type == event_type)
    if source_id:
        query = _apply_source_id_filter(query, db, source_id)

    if scoped_to_object:
        query = query.order_by(DecisionTraceEvent.created_at.asc(), DecisionTraceEvent.id.asc())
    else:
        query = query.order_by(DecisionTraceEvent.created_at.desc(), DecisionTraceEvent.id.desc())

    items = [_serialize_event(event) for event in query.limit(limit).all()]
    return {"count": len(items), "items": items}
