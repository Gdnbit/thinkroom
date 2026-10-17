"""Validate the local main-workflow evaluation manifest.

This module validates the quality-contract manifest itself. The companion
`main_workflow_eval_runner` module turns implemented cases into a deduplicated
command plan and can execute deterministic gates on demand. The manifest creates
a durable checklist for production readiness and marks which cases already have
executable gates.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_CASE_IDS = {
    "selected-source-chat-cites-only-selected-source",
    "unsupported-question-refuses-insufficient-evidence",
    "actionable-selected-source-refuses-when-directional-evidence-missing",
    "time-sensitive-selected-source-refuses-when-time-window-evidence-missing",
    "current-selected-source-refuses-when-temporal-evidence-is-stale",
    "selected-source-refuses-when-requested-horizon-conflicts-with-evidence-horizon",
    "selected-source-refuses-when-structured-claim-horizon-conflicts-with-question",
    "selected-source-refuses-when-structured-claim-validity-window-is-not-active",
    "briefing-selected-source-refuses-when-actionable-evidence-missing",
    "briefing-renders-required-sections",
    "briefing-detail-preserves-quality-filters-and-source-scope",
    "committee-selected-source-refuses-when-actionable-evidence-missing",
    "committee-detail-preserves-quality-filters-and-source-scope",
    "committee-detail-renders-trust-receipt-and-source-scope",
    "committee-records-seat-traces",
    "upload-ingestion-progress-reaches-source-id",
    "answer-output-shape-no-raw-json-or-prompt-leak",
    "answer-integrity-blocks-uncited-material-claims",
    "decision-quality-controlled-contract-validates",
    "frontend-production-build-passes",
    "live-smoke-records-llm-trace-cost-tokens",
    "trace-debate-synthetic-demo",
}

ALLOWED_PRIORITIES = {"P0", "P1"}
ALLOWED_WORKFLOWS = {"ingestion", "chat", "briefing", "committee", "answer_integrity", "frontend", "live_smoke"}
ALLOWED_MODES = {"deterministic", "live_optional"}
ALLOWED_COVERAGE_STATUS = {"implemented", "planned"}


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[4] / "evals" / "main-workflow" / "manifest.json"


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path else default_manifest_path()
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _case_errors(case: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = case.get("id") or "<missing-id>"

    if not isinstance(case.get("id"), str) or not case["id"].strip():
        errors.append("case missing id")
    if case.get("priority") not in ALLOWED_PRIORITIES:
        errors.append(f"{case_id}: invalid priority")
    if case.get("workflow") not in ALLOWED_WORKFLOWS:
        errors.append(f"{case_id}: invalid workflow")
    if case.get("mode") not in ALLOWED_MODES:
        errors.append(f"{case_id}: invalid mode")
    if case.get("coverage_status") not in ALLOWED_COVERAGE_STATUS:
        errors.append(f"{case_id}: invalid coverage_status")
    if not isinstance(case.get("fixture"), dict) or not case["fixture"].get("type"):
        errors.append(f"{case_id}: fixture.type required")
    if not isinstance(case.get("command"), str) or not case["command"].strip():
        errors.append(f"{case_id}: command required")
    if not isinstance(case.get("acceptance_checks"), list) or len(case["acceptance_checks"]) < 2:
        errors.append(f"{case_id}: at least two acceptance_checks required")
    if not isinstance(case.get("evidence_artifacts"), list) or not case["evidence_artifacts"]:
        errors.append(f"{case_id}: at least one evidence_artifact required")
    if case.get("coverage_status") == "planned" and not case.get("gap"):
        errors.append(f"{case_id}: planned case must explain gap")
    return errors


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        cases = []
        errors.append("manifest.cases must be a list")

    case_ids = [case.get("id") for case in cases if isinstance(case, dict) and isinstance(case.get("id"), str)]
    duplicate_case_ids = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    missing_required_cases = sorted(REQUIRED_CASE_IDS - set(case_ids))

    if duplicate_case_ids:
        errors.append(f"duplicate case ids: {', '.join(duplicate_case_ids)}")
    if missing_required_cases:
        errors.append(f"missing required case ids: {', '.join(missing_required_cases)}")

    for case in cases:
        if not isinstance(case, dict):
            errors.append("case must be an object")
            continue
        errors.extend(_case_errors(case))

    deterministic_cases = sum(1 for case in cases if isinstance(case, dict) and case.get("mode") == "deterministic")
    live_optional_cases = sum(1 for case in cases if isinstance(case, dict) and case.get("mode") == "live_optional")
    implemented_cases = sum(1 for case in cases if isinstance(case, dict) and case.get("coverage_status") == "implemented")
    planned_cases = sum(1 for case in cases if isinstance(case, dict) and case.get("coverage_status") == "planned")

    if deterministic_cases < 6:
        errors.append("at least six deterministic cases are required")
    if live_optional_cases < 1:
        errors.append("at least one live_optional case is required")

    return {
        "report_type": "main_workflow_eval_manifest",
        "status": "passed" if not errors else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_id": manifest.get("manifest_id"),
        "version": manifest.get("version"),
        "total_cases": len(cases),
        "deterministic_cases": deterministic_cases,
        "live_optional_cases": live_optional_cases,
        "implemented_cases": implemented_cases,
        "planned_cases": planned_cases,
        "case_ids": case_ids,
        "missing_required_cases": missing_required_cases,
        "duplicate_case_ids": duplicate_case_ids,
        "errors": errors,
    }


def run_manifest_validation(
    *,
    manifest_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved_manifest_path = Path(manifest_path) if manifest_path else default_manifest_path()
    manifest = load_manifest(resolved_manifest_path)
    report = validate_manifest(manifest)
    report["source_manifest"] = str(resolved_manifest_path)

    if report_path:
        resolved_report_path = Path(report_path)
        resolved_report_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    report_path = root / "docs" / "verification" / "main_workflow_eval_manifest_latest.json"
    report = run_manifest_validation(report_path=report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
