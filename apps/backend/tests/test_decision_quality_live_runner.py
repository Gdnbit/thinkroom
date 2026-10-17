from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.verification.decision_quality_live_runner import (
    _live_audit_status,
    _route_identity_from_trials,
    build_run_plan,
    build_trial_from_response,
    select_cases,
    write_report_bundle,
)


ROOT = Path(__file__).resolve().parents[3]
SUITE_PATH = ROOT / "evals" / "decision-quality" / "suite.json"


def _suite() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def test_select_cases_is_explicit_and_rejects_unknown_ids() -> None:
    suite = _suite()
    selected = select_cases(suite, ["dq-direct-fact-cn-alpha", "dq-geography-mismatch"])
    assert [item["id"] for item in selected] == ["dq-direct-fact-cn-alpha", "dq-geography-mismatch"]

    with pytest.raises(ValueError, match="unknown case ids"):
        select_cases(suite, ["missing-case"])


def test_build_run_plan_discloses_append_only_records_and_external_call_count() -> None:
    plan = build_run_plan(_suite(), case_ids=["dq-direct-fact-cn-alpha"], repeats=3, candidate_id="current-route")
    assert plan["status"] == "planned"
    assert plan["case_count"] == 1
    assert plan["trial_count"] == 3
    assert plan["external_model_call_requests"] == 3
    assert plan["database_policy"] == "append_only_no_cleanup"
    assert plan["candidate_id"] == "current-route"


def test_build_trial_from_response_remaps_runtime_source_ids_to_logical_fixture_ids() -> None:
    response = {
        "id": "message-1",
        "content": "截至 2026年06月30日，中国区项目 Alpha 在第二季度共有 128 份审计样本 [1]。",
        "citations": [{"index": 1, "source_id": "runtime-source-a", "summary": "evidence"}],
        "scope": {"mode": "selected_sources", "source_ids": ["runtime-source-a"]},
        "quality_report": {"decision_ready": True, "warnings": [], "evidence": {}},
        "decision_guardrail": {"status": "passed", "effective_action": "watch"},
        "llm_trace": {"mock": False, "total_cost_usd": 0.001},
        "route_identity": {"fingerprint": "sha256:route-a", "scope": "decision_chat_selected_source"},
        "timing_trace": {"total_ms": 1234},
    }
    trial = build_trial_from_response(
        case_id="dq-direct-fact-cn-alpha",
        candidate_id="current-route",
        trial_id="trial-1",
        response=response,
        runtime_to_logical={"runtime-source-a": "dq-direct-cn"},
    )

    assert trial["message_id"] == "message-1"
    assert trial["citations"][0]["source_id"] == "dq-direct-cn"
    assert trial["scope"]["source_ids"] == ["dq-direct-cn"]
    assert trial["quality_report"] == response["quality_report"]
    assert trial["llm_trace"] == response["llm_trace"]
    assert trial["route_identity"] == response["route_identity"]


def test_route_identity_requires_every_trial_to_use_one_stable_route() -> None:
    identity = {"fingerprint": "sha256:route-a", "scope": "decision_chat_selected_source"}
    stable, errors = _route_identity_from_trials(
        [
            {"case_id": "case-a", "trial_id": "trial-1", "route_identity": identity},
            {"case_id": "case-b", "trial_id": "trial-2", "route_identity": identity},
        ]
    )
    assert stable == identity
    assert errors == []

    missing, missing_errors = _route_identity_from_trials(
        [{"case_id": "case-a", "trial_id": "trial-1", "route_identity": {}}]
    )
    assert missing == {}
    assert missing_errors[0]["error_type"] == "route_identity_missing"

    mixed, mixed_errors = _route_identity_from_trials(
        [
            {"case_id": "case-a", "trial_id": "trial-1", "route_identity": identity},
            {"case_id": "case-b", "trial_id": "trial-2", "route_identity": {"fingerprint": "sha256:route-b"}},
        ]
    )
    assert mixed == {}
    assert mixed_errors[0]["error_type"] == "route_identity_changed_during_audit"


def test_report_bundle_preserves_timestamped_history(tmp_path: Path) -> None:
    latest = tmp_path / "decision-quality-live-audit-latest.json"
    first = {"report_type": "decision_quality_live_audit", "status": "failed", "generated_at": "2026-07-10T18:00:00+00:00"}
    second = {"report_type": "decision_quality_live_audit", "status": "passed", "generated_at": "2026-07-10T19:00:00+00:00"}

    first_archive, _ = write_report_bundle(first, latest)
    second_archive, _ = write_report_bundle(second, latest)

    assert first_archive.exists()
    assert second_archive.exists()
    assert first_archive != second_archive
    assert json.loads(first_archive.read_text(encoding="utf-8"))["status"] == "failed"
    assert json.loads(latest.read_text(encoding="utf-8"))["status"] == "passed"


def test_live_audit_status_requires_full_repeated_candidate_gate() -> None:
    grades = [{"status": "passed"}]

    assert _live_audit_status(
        errors=[],
        grades=grades,
        evaluation={"candidates": {"baseline": {"status": "failed"}}},
        candidate_id="baseline",
    ) == "failed"
    assert _live_audit_status(
        errors=[],
        grades=grades,
        evaluation={"candidates": {"baseline": {"status": "passed"}}},
        candidate_id="baseline",
    ) == "passed"
