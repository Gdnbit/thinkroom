"""Build replayable eval cases from persisted decision trace events."""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.schemas.decision_trace_event import DecisionTraceEvent
from app.services.trace_eval_review_service import (
    get_trace_eval_review_map,
    recommended_next_step_for_review_status,
    serialize_trace_eval_review,
)


_WORKFLOW_BY_OBJECT_TYPE = {
    "chat_message": "chat",
    "briefing": "briefing",
    "debate": "committee",
}

_DEFAULT_FILTER_SCAN_LIMIT = 1000


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _warning_codes(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    return [
        str(item.get("code")).strip()
        for item in _safe_list(payload.get("warnings"))
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    ]


def _event_reasons(event: DecisionTraceEvent) -> list[str]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.event_type == "quality" and event.status != "ok":
        warning_codes = _warning_codes(payload)
        return [f"quality:{code}" for code in warning_codes] or ["quality:decision_not_ready"]
    if event.event_type == "guardrail" and event.status != "ok":
        status = str(payload.get("status") or event.status or "warning").strip()
        return [f"guardrail:{status}"]
    if event.event_type == "llm_call":
        reasons: list[str] = []
        error_type = str(payload.get("error_type") or "").strip()
        if error_type:
            reasons.append(f"llm:{error_type}")
        if str(event.step_name or payload.get("step") or "").strip() == "integrity_retry":
            reasons.append("llm:integrity_retry")
        elif payload.get("integrity_reasons"):
            reasons.append("llm:integrity_retry")
        if event.status in {"error", "mock"} and not reasons:
            reasons.append(f"llm:{event.status}")
        return reasons
    return []


def _case_id(related_object_type: str, related_object_id: str) -> str:
    safe_object_type = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in related_object_type)
    safe_object_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in related_object_id)
    return f"trace-{safe_object_type}-{safe_object_id}"


def _dedupe(values: list[str]) -> list[str]:
    return list(OrderedDict((value, None) for value in values if value).keys())


def _normalize_filter_value(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or normalized == "all":
        return None
    return normalized


def _count_by(items: list[dict[str, Any]], field_path: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        current: Any = item
        for key in field_path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if not isinstance(current, str) or not current.strip():
            continue
        key = current.strip()
        counts[key] = counts.get(key, 0) + 1
    return counts


def _candidate_search_text(item: dict[str, Any]) -> str:
    candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
    review_decision = item.get("review_decision") if isinstance(item.get("review_decision"), dict) else {}
    fixture = candidate.get("fixture") if isinstance(candidate.get("fixture"), dict) else {}
    values: list[Any] = [
        item.get("review_status"),
        item.get("recommended_next_step"),
        item.get("main_manifest_case_id"),
        review_decision.get("decision_note"),
        review_decision.get("reviewer"),
        candidate.get("id"),
        candidate.get("workflow"),
        candidate.get("mode"),
        candidate.get("coverage_status"),
        candidate.get("trace_event_count"),
        *(_safe_list(candidate.get("trigger_reasons"))),
        *(_safe_list(candidate.get("acceptance_checks"))),
        *(_safe_list(candidate.get("evidence_artifacts"))),
        fixture.get("related_object_type"),
        fixture.get("related_object_id"),
        fixture.get("run_id"),
        fixture.get("session_id"),
        *(_safe_list(fixture.get("source_ids"))),
    ]
    return " ".join(str(value).lower() for value in values if value is not None)


def _filter_review_candidates(
    candidates: list[dict[str, Any]],
    *,
    review_status: str | None = None,
    workflow: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    status_filter = _normalize_filter_value(review_status)
    workflow_filter = _normalize_filter_value(workflow)
    query_filter = _normalize_filter_value(query)
    query_filter = query_filter.lower() if query_filter else None

    filtered: list[dict[str, Any]] = []
    for item in candidates:
        candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        if status_filter and item.get("review_status") != status_filter:
            continue
        if workflow_filter and candidate.get("workflow") != workflow_filter:
            continue
        if query_filter and query_filter not in _candidate_search_text(item):
            continue
        filtered.append(item)
    return filtered


def _acceptance_checks(reasons: list[str]) -> list[str]:
    checks = [
        "replay must preserve original source scope and trace metadata",
        "quality report must expose the same blocking or warning reason",
    ]
    if "guardrail:blocked_output" in reasons:
        checks.append("final answer must not expose blocked model output")
    if "llm:integrity_retry" in reasons:
        checks.append("integrity retry must be visible and deterministic on replay")
    if any(reason.startswith("quality:") for reason in reasons):
        checks.append("trust receipt must surface quality warning chips")
    if any(reason.startswith("guardrail:") for reason in reasons):
        checks.append("decision guardrail status must be preserved")
    return _dedupe(checks)


def _case_from_events(events: list[DecisionTraceEvent]) -> dict[str, Any] | None:
    if not events:
        return None
    ordered_events = sorted(events, key=lambda item: (item.created_at, item.id))
    first = ordered_events[0]
    reasons = _dedupe([
        reason
        for event in ordered_events
        for reason in _event_reasons(event)
    ])
    if not reasons:
        return None
    source_ids = _dedupe([
        str(source_id)
        for event in ordered_events
        for source_id in _safe_list(event.source_ids)
        if str(source_id or "").strip()
    ])
    return {
        "id": _case_id(first.related_object_type, first.related_object_id),
        "priority": "P0",
        "workflow": _WORKFLOW_BY_OBJECT_TYPE.get(first.related_object_type, first.related_object_type),
        "mode": "deterministic",
        "coverage_status": "generated_from_trace",
        "fixture": {
            "type": "decision_trace_replay",
            "related_object_type": first.related_object_type,
            "related_object_id": first.related_object_id,
            "run_id": first.run_id,
            "session_id": first.session_id,
            "source_ids": source_ids,
        },
        "trigger_reasons": reasons,
        "trace_event_count": len(ordered_events),
        "trace_event_ids": [event.id for event in ordered_events],
        "acceptance_checks": _acceptance_checks(reasons),
        "evidence_artifacts": [
            "decision_trace_events",
            "quality_report",
            "decision_guardrail",
        ],
    }


def collect_trace_eval_cases(db: Session, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent trace-derived eval cases for warning/error/retry paths."""

    recent_rows = (
        db.query(DecisionTraceEvent)
        .order_by(DecisionTraceEvent.created_at.desc(), DecisionTraceEvent.id.desc())
        .limit(max(limit * 8, 50))
        .all()
    )
    grouped: OrderedDict[tuple[str, str], list[DecisionTraceEvent]] = OrderedDict()
    for event in recent_rows:
        key = (event.related_object_type, event.related_object_id)
        grouped.setdefault(key, []).append(event)

    cases: list[dict[str, Any]] = []
    for related_object_type, related_object_id in grouped:
        events = (
            db.query(DecisionTraceEvent)
            .filter(
                DecisionTraceEvent.related_object_type == related_object_type,
                DecisionTraceEvent.related_object_id == related_object_id,
            )
            .order_by(DecisionTraceEvent.created_at.asc(), DecisionTraceEvent.id.asc())
            .all()
        )
        case = _case_from_events(events)
        if case:
            cases.append(case)
        if len(cases) >= limit:
            break
    return cases


def default_trace_eval_review_manifest_path() -> Path:
    return Path(__file__).resolve().parents[4] / "docs" / "verification" / "trace_eval_review_manifest_latest.json"


def _load_main_manifest_case_ids(main_manifest_path: str | Path | None = None) -> tuple[set[str], str | None]:
    from app.verification.main_workflow_eval_manifest import default_manifest_path

    resolved_path = Path(main_manifest_path) if main_manifest_path else default_manifest_path()
    try:
        manifest = json.loads(resolved_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(), f"main workflow eval manifest not found: {resolved_path}"
    except json.JSONDecodeError as exc:
        return set(), f"main workflow eval manifest invalid json: {exc}"

    cases = manifest.get("cases")
    if not isinstance(cases, list):
        return set(), "main workflow eval manifest cases must be a list"
    return {case["id"] for case in cases if isinstance(case, dict) and isinstance(case.get("id"), str)}, None


def build_trace_eval_review_manifest(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    review_status: str | None = None,
    workflow: str | None = None,
    query: str | None = None,
    scan_limit: int | None = None,
    main_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Wrap generated trace cases in a human-review manifest.

    Generated trace candidates intentionally stay outside the curated main eval
    manifest until a human reviews the fixture and assigns an executable gate.
    """

    page_limit = max(1, int(limit))
    page_offset = max(0, int(offset))
    has_filter_or_offset = bool(
        page_offset
        or _normalize_filter_value(review_status)
        or _normalize_filter_value(workflow)
        or _normalize_filter_value(query)
    )
    candidate_scan_limit = max(
        page_limit + page_offset,
        int(scan_limit)
        if scan_limit is not None
        else (_DEFAULT_FILTER_SCAN_LIMIT if has_filter_or_offset else page_limit),
    )

    cases = collect_trace_eval_cases(db, limit=candidate_scan_limit)
    main_manifest_case_ids, coverage_error = _load_main_manifest_case_ids(main_manifest_path)
    review_map = get_trace_eval_review_map(db, [case["id"] for case in cases])
    candidates: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["id"]
        review = review_map.get(case_id)
        if case_id in main_manifest_case_ids:
            item = {
                "review_status": "already_covered",
                "recommended_next_step": "no_action_required_trace_already_in_main_manifest",
                "main_manifest_case_id": case_id,
                "candidate": case,
            }
            if review:
                item["review_decision"] = serialize_trace_eval_review(review)
            candidates.append(item)
        elif review:
            candidates.append({
                "review_status": review.review_status,
                "recommended_next_step": recommended_next_step_for_review_status(review.review_status),
                "review_decision": serialize_trace_eval_review(review),
                "candidate": case,
            })
        else:
            candidates.append({
                "review_status": "pending_review",
                "recommended_next_step": "review_before_promoting_to_main_manifest",
                "candidate": case,
            })

    filtered_candidates = _filter_review_candidates(
        candidates,
        review_status=review_status,
        workflow=workflow,
        query=query,
    )
    paged_candidates = filtered_candidates[page_offset:page_offset + page_limit]

    pending_candidates = sum(1 for item in candidates if item["review_status"] == "pending_review")
    covered_candidates = sum(1 for item in candidates if item["review_status"] == "already_covered")
    reviewed_candidates = len(candidates) - pending_candidates - covered_candidates
    if not cases:
        status = "empty"
    elif pending_candidates:
        status = "review_required"
    elif reviewed_candidates:
        status = "reviewed"
    else:
        status = "covered"

    return {
        "report_type": "trace_eval_review_manifest",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "decision_trace_events",
        "non_destructive": True,
        "main_manifest_mutated": False,
        "total_candidates": len(cases),
        "pending_candidates": pending_candidates,
        "covered_candidates": covered_candidates,
        "reviewed_candidates": reviewed_candidates,
        "main_manifest_coverage_loaded": coverage_error is None,
        "main_manifest_coverage_error": coverage_error,
        "filtered_candidates": len(filtered_candidates),
        "returned_candidates": len(paged_candidates),
        "limit": page_limit,
        "offset": page_offset,
        "candidate_scan_limit": candidate_scan_limit,
        "filters": {
            "review_status": _normalize_filter_value(review_status),
            "workflow": _normalize_filter_value(workflow),
            "query": _normalize_filter_value(query),
        },
        "status_counts": _count_by(candidates, ("review_status",)),
        "workflow_counts": _count_by(candidates, ("candidate", "workflow")),
        "review_policy": [
            "do not auto-promote generated trace candidates",
            "verify fixture scope and evidence before adding to the main workflow eval manifest",
            "assign or implement an executable command before promotion",
        ],
        "candidates": paged_candidates,
    }


def write_trace_eval_review_manifest(
    db: Session,
    *,
    output_path: str | Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    resolved_output_path = Path(output_path) if output_path else default_trace_eval_review_manifest_path()
    manifest = build_trace_eval_review_manifest(db, limit=limit)
    manifest["output_path"] = str(resolved_output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export trace-derived eval candidates for human review.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", default=str(default_trace_eval_review_manifest_path()))
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        manifest = write_trace_eval_review_manifest(db, output_path=args.output, limit=args.limit)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "build_trace_eval_review_manifest",
    "collect_trace_eval_cases",
    "default_trace_eval_review_manifest_path",
    "write_trace_eval_review_manifest",
]


if __name__ == "__main__":
    sys.exit(main())
