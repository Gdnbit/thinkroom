"""Export and validate self-contained trace eval replay fixtures."""
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
from app.verification.trace_eval_corpus import collect_trace_eval_cases


def default_trace_eval_replay_fixture_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "evals" / "main-workflow" / "trace_eval_replay_fixtures"


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[str]) -> list[str]:
    return list(OrderedDict((value, None) for value in values if value).keys())


def _warning_codes(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    return [
        str(item.get("code")).strip()
        for item in _safe_list(payload.get("warnings"))
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    ]


def _snapshot_event_reasons(event: dict[str, Any]) -> list[str]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    status = str(event.get("status") or "")
    if event.get("event_type") == "quality" and status != "ok":
        warning_codes = _warning_codes(payload)
        return [f"quality:{code}" for code in warning_codes] or ["quality:decision_not_ready"]
    if event.get("event_type") == "guardrail" and status != "ok":
        guardrail_status = str(payload.get("status") or status or "warning").strip()
        return [f"guardrail:{guardrail_status}"]
    if event.get("event_type") == "llm_call":
        reasons: list[str] = []
        error_type = str(payload.get("error_type") or "").strip()
        if error_type:
            reasons.append(f"llm:{error_type}")
        step = str(event.get("step_name") or payload.get("step") or "").strip()
        if step == "integrity_retry" or payload.get("integrity_reasons"):
            reasons.append("llm:integrity_retry")
        if status in {"error", "mock"} and not reasons:
            reasons.append(f"llm:{status}")
        return reasons
    return []


def _serialize_event(event: DecisionTraceEvent) -> dict[str, Any]:
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
        "duration_ms": event.duration_ms,
        "cost_usd": event.cost_usd,
        "source_ids": event.source_ids or [],
        "payload": event.payload or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }


def _find_case(db: Session, candidate_id: str) -> dict[str, Any]:
    for case in collect_trace_eval_cases(db, limit=1000):
        if case.get("id") == candidate_id:
            return case
    raise ValueError(f"Trace eval candidate not found: {candidate_id}")


def build_trace_eval_replay_fixture(db: Session, *, candidate_id: str) -> dict[str, Any]:
    candidate = _find_case(db, candidate_id)
    fixture = candidate["fixture"]
    events = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == fixture["related_object_type"],
            DecisionTraceEvent.related_object_id == fixture["related_object_id"],
        )
        .order_by(DecisionTraceEvent.created_at.asc(), DecisionTraceEvent.id.asc())
        .all()
    )
    serialized_events = [_serialize_event(event) for event in events]
    return {
        "fixture_type": "decision_trace_replay_fixture",
        "version": "2026-07-04",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_id": candidate_id,
        "workflow": candidate["workflow"],
        "fixture": fixture,
        "expected": {
            "trace_event_count": candidate["trace_event_count"],
            "trace_event_ids": candidate["trace_event_ids"],
            "trigger_reasons": candidate["trigger_reasons"],
            "source_ids": fixture.get("source_ids", []),
        },
        "acceptance_checks": candidate["acceptance_checks"],
        "evidence_artifacts": candidate["evidence_artifacts"],
        "events": serialized_events,
    }


def _fixture_filename(candidate_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in candidate_id)
    return f"{safe}.json"


def write_trace_eval_replay_fixture(
    db: Session,
    *,
    candidate_id: str,
    output_dir: str | Path | None = None,
) -> Path:
    resolved_output_dir = Path(output_dir) if output_dir else default_trace_eval_replay_fixture_dir()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    output_path = resolved_output_dir / _fixture_filename(candidate_id)
    fixture = build_trace_eval_replay_fixture(db, candidate_id=candidate_id)
    output_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def validate_trace_eval_replay_fixture(path: str | Path) -> dict[str, Any]:
    fixture_path = Path(path)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    events = fixture.get("events")
    if fixture.get("fixture_type") != "decision_trace_replay_fixture":
        errors.append("fixture_type mismatch")
    if not isinstance(events, list) or not events:
        errors.append("events required")
        events = []

    expected = fixture.get("expected") if isinstance(fixture.get("expected"), dict) else {}
    event_ids = [str(event.get("id")) for event in events if isinstance(event, dict) and event.get("id")]
    source_ids = _dedupe([
        str(source_id)
        for event in events
        if isinstance(event, dict)
        for source_id in _safe_list(event.get("source_ids"))
        if str(source_id or "").strip()
    ])
    trigger_reasons = _dedupe([
        reason
        for event in events
        if isinstance(event, dict)
        for reason in _snapshot_event_reasons(event)
    ])

    if expected.get("trace_event_count") != len(events):
        errors.append("trace_event_count mismatch")
    if expected.get("trace_event_ids") != event_ids:
        errors.append("trace_event_ids mismatch")
    if len(set(event_ids)) != len(event_ids):
        errors.append("duplicate trace_event_ids")
    if expected.get("source_ids") != source_ids:
        errors.append("source_ids mismatch")
    if expected.get("trigger_reasons") != trigger_reasons:
        errors.append("trigger_reasons mismatch")

    return {
        "report_type": "trace_eval_replay_validation",
        "status": "passed" if not errors else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture_path": str(fixture_path),
        "candidate_id": fixture.get("candidate_id"),
        "trace_event_count": len(events),
        "trigger_reasons": trigger_reasons,
        "source_ids": source_ids,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export or validate trace eval replay fixtures.")
    parser.add_argument("--candidate-id")
    parser.add_argument("--output-dir", default=str(default_trace_eval_replay_fixture_dir()))
    parser.add_argument("--fixture")
    args = parser.parse_args(argv)

    if args.fixture:
        report = validate_trace_eval_replay_fixture(args.fixture)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    if not args.candidate_id:
        parser.error("--candidate-id is required when --fixture is not provided")
    with SessionLocal() as db:
        output_path = write_trace_eval_replay_fixture(db, candidate_id=args.candidate_id, output_dir=args.output_dir)
    print(json.dumps({"status": "written", "output_path": str(output_path)}, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "build_trace_eval_replay_fixture",
    "default_trace_eval_replay_fixture_dir",
    "validate_trace_eval_replay_fixture",
    "write_trace_eval_replay_fixture",
]


if __name__ == "__main__":
    sys.exit(main())
