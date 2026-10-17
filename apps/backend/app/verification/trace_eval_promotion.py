"""Promote reviewed trace-derived eval candidates into manifest drafts."""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.verification.main_workflow_eval_manifest import default_manifest_path, validate_manifest
from app.verification.trace_eval_corpus import default_trace_eval_review_manifest_path
from app.verification.trace_eval_replay import validate_trace_eval_replay_fixture


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _find_candidate(review_manifest: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for item in review_manifest.get("candidates", []):
        if not isinstance(item, dict):
            continue
        candidate = item.get("candidate")
        if isinstance(candidate, dict) and candidate.get("id") == candidate_id:
            return candidate
    raise ValueError(f"Trace eval candidate not found: {candidate_id}")


def _candidate_to_manifest_case(
    candidate: dict[str, Any],
    *,
    command: str,
    reviewer_note: str,
    coverage_status: str,
    replay_fixture_path: str | Path | None,
) -> dict[str, Any]:
    if not command.strip():
        raise ValueError("command is required before promotion")
    if not reviewer_note.strip():
        raise ValueError("reviewer_note is required before promotion")
    if coverage_status not in {"planned", "implemented"}:
        raise ValueError("coverage_status must be planned or implemented")

    evidence_artifacts = [
        *(candidate.get("evidence_artifacts") or ["decision_trace_events"]),
        "docs/verification/trace_eval_review_manifest_latest.json",
    ]
    case_fixture = candidate.get("fixture") or {"type": "decision_trace_replay"}
    gap = "Trace-derived candidate needs an implemented replay fixture before marking implemented."

    if coverage_status == "implemented":
        if not replay_fixture_path:
            raise ValueError("replay_fixture_path is required for implemented promotion")
        validation = validate_trace_eval_replay_fixture(replay_fixture_path)
        if validation["status"] != "passed":
            raise ValueError(f"replay fixture validation failed: {validation['errors']}")
        case_fixture = {
            "type": "decision_trace_replay_fixture",
            "path": str(Path(replay_fixture_path)),
            "candidate_id": candidate["id"],
        }
        evidence_artifacts.append(str(Path(replay_fixture_path)))
        gap = ""

    case = {
        "id": candidate["id"],
        "priority": candidate.get("priority", "P0"),
        "workflow": candidate.get("workflow"),
        "mode": candidate.get("mode", "deterministic"),
        "coverage_status": coverage_status,
        "fixture": case_fixture,
        "command": command.strip(),
        "acceptance_checks": candidate.get("acceptance_checks") or [
            "replay must preserve original source scope and trace metadata",
            "quality report must expose the same blocking or warning reason",
        ],
        "evidence_artifacts": evidence_artifacts,
        "risk_if_missing": "A historical production trace failure could regress without replay coverage.",
        "reviewer_note": reviewer_note.strip(),
        "trace_trigger_reasons": candidate.get("trigger_reasons", []),
        "trace_event_ids": candidate.get("trace_event_ids", []),
    }
    if gap:
        case["gap"] = gap
    return case


def promote_trace_eval_candidate_to_manifest_draft(
    *,
    review_manifest_path: str | Path,
    main_manifest_path: str | Path,
    candidate_id: str,
    command: str,
    reviewer_note: str,
    output_path: str | Path,
    coverage_status: str = "planned",
    replay_fixture_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append one reviewed trace candidate to a draft main eval manifest."""

    review_manifest = _load_json(review_manifest_path)
    main_manifest = _load_json(main_manifest_path)
    candidate = _find_candidate(review_manifest, candidate_id)
    existing_ids = {
        case.get("id")
        for case in main_manifest.get("cases", [])
        if isinstance(case, dict)
    }
    if candidate_id in existing_ids:
        raise ValueError(f"Main workflow eval case already exists: {candidate_id}")

    draft_manifest = deepcopy(main_manifest)
    draft_manifest.setdefault("cases", []).append(
        _candidate_to_manifest_case(
            candidate,
            command=command,
            reviewer_note=reviewer_note,
            coverage_status=coverage_status,
            replay_fixture_path=replay_fixture_path,
        )
    )
    validation = validate_manifest(draft_manifest)
    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(json.dumps(draft_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "report_type": "trace_eval_promotion",
        "status": "draft_written",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_id": candidate_id,
        "main_manifest_mutated": False,
        "source_review_manifest": str(Path(review_manifest_path)),
        "source_main_manifest": str(Path(main_manifest_path)),
        "output_path": str(resolved_output_path),
        "manifest_validation": validation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote one reviewed trace eval candidate to a manifest draft.")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--reviewer-note", required=True)
    parser.add_argument("--review-manifest", default=str(default_trace_eval_review_manifest_path()))
    parser.add_argument("--main-manifest", default=str(default_manifest_path()))
    parser.add_argument("--output", required=True)
    parser.add_argument("--coverage-status", choices=["planned", "implemented"], default="planned")
    parser.add_argument("--replay-fixture")
    args = parser.parse_args(argv)

    report = promote_trace_eval_candidate_to_manifest_draft(
        review_manifest_path=args.review_manifest,
        main_manifest_path=args.main_manifest,
        candidate_id=args.candidate_id,
        command=args.command,
        reviewer_note=args.reviewer_note,
        output_path=args.output,
        coverage_status=args.coverage_status,
        replay_fixture_path=args.replay_fixture,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["manifest_validation"]["status"] == "passed" else 1


__all__ = ["promote_trace_eval_candidate_to_manifest_draft"]


if __name__ == "__main__":
    sys.exit(main())
