"""Decision run trace persistence helpers."""

from __future__ import annotations

from typing import Any, Iterable

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
from ulid import ULID

from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink
from app.utils.public_llm_trace import public_llm_trace
from app.utils.source_scope import clean_source_ids


def _jsonable(value: Any) -> Any:
    return jsonable_encoder(value if value is not None else {})


def _status_from_payload(payload: dict[str, Any]) -> str:
    if payload.get("error_type"):
        return "error"
    if payload.get("mock") is True or payload.get("is_mock") is True:
        return "mock"
    return str(payload.get("status") or "ok")[:20]


def _source_ids_from_scope(scope: Any) -> list[str]:
    if not isinstance(scope, dict):
        return []
    candidates: list[str] = []
    candidates.extend(clean_source_ids(scope.get("source_ids")))
    source_scope = scope.get("source_scope")
    if isinstance(source_scope, dict):
        candidates.extend(clean_source_ids(source_scope.get("requested_source_ids")))
        sources = source_scope.get("sources")
        if isinstance(sources, list):
            candidates.extend(clean_source_ids(
                source.get("source_id")
                for source in sources
                if isinstance(source, dict)
            ))
    return clean_source_ids(candidates)


def _normalize_llm_calls(llm_trace: Any) -> list[dict[str, Any]]:
    projected = public_llm_trace(llm_trace)
    if not projected:
        return []

    calls = projected.get("calls")
    if isinstance(calls, list):
        return [call for call in calls if isinstance(call, dict)]
    return [projected]


def record_trace_event(
    db: Session,
    *,
    related_object_type: str,
    related_object_id: str,
    event_type: str,
    step_name: str | None = None,
    status: str = "ok",
    run_id: str | None = None,
    session_id: str | None = None,
    job_id: str | None = None,
    source_ids: Iterable[Any] | None = None,
    model: str | None = None,
    skill_role: str | None = None,
    duration_ms: int | None = None,
    cost_usd: float | None = None,
    payload: Any | None = None,
) -> DecisionTraceEvent:
    cleaned_source_ids = clean_source_ids(source_ids)
    event = DecisionTraceEvent(
        id=str(ULID()),
        related_object_type=related_object_type,
        related_object_id=related_object_id,
        run_id=run_id,
        session_id=session_id,
        job_id=job_id,
        event_type=event_type,
        step_name=step_name,
        status=(status or "ok")[:20],
        model=model,
        skill_role=skill_role,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        source_ids=cleaned_source_ids,
        payload=_jsonable(payload),
    )
    event.source_links = [
        DecisionTraceSourceLink(trace_event_id=event.id, source_id=source_id)
        for source_id in cleaned_source_ids
    ]
    db.add(event)
    return event


def record_decision_trace_bundle(
    db: Session,
    *,
    related_object_type: str,
    related_object_id: str,
    run_id: str | None = None,
    session_id: str | None = None,
    job_id: str | None = None,
    source_ids: Iterable[Any] | None = None,
    scope: Any | None = None,
    quality_report: Any | None = None,
    decision_guardrail: Any | None = None,
    llm_trace: Any | None = None,
) -> list[DecisionTraceEvent]:
    """Persist the standard trace bundle for a decision-facing output.

    The helper intentionally does not commit. Callers add events to the same
    transaction as the output object so traces cannot drift from saved results.
    """

    normalized_source_ids = clean_source_ids(source_ids) or _source_ids_from_scope(scope)
    events: list[DecisionTraceEvent] = []

    if scope:
        events.append(record_trace_event(
            db,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            run_id=run_id,
            session_id=session_id,
            job_id=job_id,
            event_type="scope",
            step_name="source_scope",
            source_ids=normalized_source_ids,
            payload=scope,
        ))

    if quality_report:
        status = "ok"
        if isinstance(quality_report, dict) and not quality_report.get("decision_ready", True):
            status = "warning"
        events.append(record_trace_event(
            db,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            run_id=run_id,
            session_id=session_id,
            job_id=job_id,
            event_type="quality",
            step_name="quality_report",
            status=status,
            source_ids=normalized_source_ids,
            payload=quality_report,
        ))

    if decision_guardrail:
        guardrail_status = "ok"
        if isinstance(decision_guardrail, dict):
            guardrail_status = (
                "warning"
                if decision_guardrail.get("status") in {"downgraded", "blocked", "blocked_output"}
                else "ok"
            )
        events.append(record_trace_event(
            db,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            run_id=run_id,
            session_id=session_id,
            job_id=job_id,
            event_type="guardrail",
            step_name="decision_guardrail",
            status=guardrail_status,
            source_ids=normalized_source_ids,
            payload=decision_guardrail,
        ))

    for index, call in enumerate(_normalize_llm_calls(llm_trace), start=1):
        payload = dict(call)
        payload["call_index"] = index
        events.append(record_trace_event(
            db,
            related_object_type=related_object_type,
            related_object_id=related_object_id,
            run_id=run_id,
            session_id=session_id,
            job_id=job_id,
            event_type="llm_call",
            step_name=str(call.get("step") or call.get("skill_role") or "llm"),
            status=_status_from_payload(call),
            model=call.get("model"),
            skill_role=call.get("skill_role"),
            duration_ms=call.get("duration_ms"),
            cost_usd=call.get("cost_usd"),
            source_ids=normalized_source_ids,
            payload=payload,
        ))

    return events
