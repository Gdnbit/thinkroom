from __future__ import annotations

import json

from app.verification.structured_workflow_quality import (
    evaluate_suite,
    grade_trial,
    load_suite,
    validate_suite,
)
from app.verification.structured_workflow_quality_live_runner import (
    _redact_trial,
    build_run_plan,
    build_trial_from_responses,
    write_report_bundle,
)


def _suite() -> dict:
    return {
        "suite_id": "test-structured-workflows",
        "version": "1.0.0",
        "workflows": ["briefing", "committee"],
        "route_scopes": {
            "briefing": "briefing_selected_source",
            "committee": "committee_selected_source",
        },
        "thresholds": {
            "min_trials_per_case": 1,
            "min_trial_pass_rate": 1.0,
            "min_fact_recall": 1.0,
            "min_scope_accuracy": 1.0,
            "max_unverified_material_fields": 0,
        },
        "cases": [
            {
                "id": "case-direct",
                "category": "direct_fact",
                "critical": True,
                "question": "How many?",
                "fixture": {
                    "sources": [
                        {
                            "source_id": "source-logical",
                            "content": "项目 Alpha 完成 128 份样本。",
                        }
                    ]
                },
                "expected": {
                    "behavior": "answer",
                    "scope_source_ids": ["source-logical"],
                    "required_fact_units": [
                        {"id": "fact-alpha", "all_terms": ["Alpha", "128"]}
                    ],
                    "required_answer_term_groups": [],
                    "forbidden_terms": ["256"],
                    "effective_actions": ["watch"],
                    "model_call_policy": "required",
                },
            }
        ],
    }


def _trial(workflow: str, *, field_status: str = "supported") -> dict:
    material_fields = {
        "briefing": [
            "direction",
            "recommended_action",
            "executive_summary",
            "supporting_points",
            "contrary_points",
            "key_risks",
        ],
        "committee": [
            "direction",
            "recommended_action",
            "synthesis",
            "risk_warnings",
            "structure_warnings",
        ],
    }[workflow]
    return {
        "workflow": workflow,
        "case_id": "case-direct",
        "trial_id": f"trial-{workflow}",
        "visible_text": "项目 Alpha 完成 128 份样本。",
        "scope": {"mode": "selected_sources", "source_ids": ["source-logical"]},
        "quality_report": {
            "decision_ready": True,
            "evidence": {
                "field_evidence": {
                    field_name: {"status": field_status}
                    for field_name in material_fields
                },
            },
        },
        "decision_guardrail": {
            "status": "passed",
            "decision_ready": True,
            "effective_action": "watch",
        },
        "llm_trace": {
            "call_count": 1,
            "total_cost_usd": 0.001,
            "calls": [
                {
                    "model": "provider/model",
                    "generation_id": "gen-1",
                    "mock": False,
                }
            ],
        },
        "route_identity": {
            "scope": f"{workflow}_selected_source",
            "fingerprint": f"sha256:{workflow}",
        },
        "duration_ms": 100,
        "persistence_ok": True,
    }


def test_checked_in_structured_workflow_suite_is_valid_and_plans_eighteen_trials() -> None:
    suite = load_suite()

    assert validate_suite(suite)["status"] == "passed"
    plan = build_run_plan(
        suite,
        case_ids=None,
        repeats=3,
        candidate_id="candidate-a",
    )
    assert plan["trial_count"] == 18
    assert plan["workflows"] == ["briefing", "committee"]


def test_grade_trial_requires_facts_scope_trace_field_evidence_and_persistence() -> None:
    grade = grade_trial(_suite(), _trial("briefing"))

    assert grade["status"] == "passed"
    assert grade["metrics"]["fact_recall"] == 1.0
    assert grade["checks"]["public_trace_safe"] is True

    failed = grade_trial(_suite(), _trial("briefing", field_status="unverified"))
    assert failed["status"] == "failed"
    assert "unverified_material_fields" in failed["failures"]


def test_grade_trial_requires_every_material_field_evidence_receipt() -> None:
    incomplete = _trial("briefing")
    incomplete["quality_report"]["evidence"]["field_evidence"].pop("key_risks")

    grade = grade_trial(_suite(), incomplete)

    assert grade["status"] == "failed"
    assert "material_field_evidence_missing" in grade["failures"]
    assert grade["missing_material_fields"] == ["key_risks"]
    assert grade["metrics"]["missing_material_field_count"] == 1


def test_grade_trial_accepts_direction_words_or_equivalent_signed_percentages() -> None:
    suite = _suite()
    suite["cases"][0]["expected"]["required_fact_units"] = [
        {
            "id": "beta-up-ten",
            "all_terms": ["Beta", "10%"],
            "any_term_groups": [["增长", "+10%"]],
        },
        {
            "id": "beta-down-six",
            "all_terms": ["Beta", "6%"],
            "any_term_groups": [["下降", "-6%"]],
        },
    ]
    trial = _trial("committee")
    trial["visible_text"] = "项目 Beta 的两份资料相互冲突：A 预计 +10%，B 预计 -6%。"

    assert validate_suite(suite)["status"] == "passed"
    grade = grade_trial(suite, trial)
    assert grade["status"] == "passed"
    assert grade["metrics"]["fact_recall"] == 1.0


def test_evaluate_suite_requires_every_workflow_group() -> None:
    missing_committee = evaluate_suite(_suite(), [_trial("briefing")])
    assert missing_committee["status"] == "failed"

    complete = evaluate_suite(_suite(), [_trial("briefing"), _trial("committee")])
    assert complete["status"] == "passed"
    assert complete["passed_trial_count"] == 2


def test_build_trial_remaps_sources_and_checks_persistence() -> None:
    response = {
        "id": "briefing-1",
        "title": "Alpha",
        "direction": "neutral",
        "recommended_action": "watch",
        "executive_summary": "项目 Alpha 完成 128 份样本。",
        "supporting_points": [],
        "contrary_points": [],
        "key_risks": [],
        "scope": {"mode": "selected_sources", "source_ids": ["source-runtime"]},
        "quality_report": {
            "decision_ready": True,
            "evidence": {
                "field_evidence": {
                    "executive_summary": {
                        "status": "supported",
                        "source_ids": ["source-runtime"],
                    }
                }
            },
        },
        "decision_guardrail": {"status": "passed", "decision_ready": True, "effective_action": "watch"},
        "llm_trace": {"call_count": 1, "calls": [{"model": "m", "generation_id": "g", "mock": False}]},
        "route_identity": {"scope": "briefing_selected_source", "fingerprint": "sha256:a"},
        "duration_ms": 100,
    }
    detail = dict(response)

    trial = build_trial_from_responses(
        workflow="briefing",
        case_id="case-direct",
        candidate_id="candidate",
        trial_id="trial",
        response=response,
        detail=detail,
        runtime_to_logical={"source-runtime": "source-logical"},
    )

    assert trial["scope"]["source_ids"] == ["source-logical"]
    assert trial["quality_report"]["evidence"]["field_evidence"]["executive_summary"]["source_ids"] == ["source-logical"]
    assert trial["persistence_ok"] is True


def test_report_bundle_preserves_previous_latest(tmp_path) -> None:
    latest = tmp_path / "structured-workflow-quality-live-audit-latest.json"
    previous = {
        "report_type": "structured_workflow_quality_live_audit",
        "status": "failed",
        "generated_at": "2026-07-11T00:00:00+00:00",
    }
    latest.write_text(json.dumps(previous), encoding="utf-8")
    current = {
        "report_type": "structured_workflow_quality_live_audit",
        "status": "passed",
        "generated_at": "2026-07-12T00:00:00+00:00",
    }

    archive, written_latest = write_report_bundle(current, latest)

    assert archive.name == "structured-workflow-quality-live-audit-20260712T000000Z.json"
    assert written_latest == latest
    assert (tmp_path / "structured-workflow-quality-live-audit-20260711T000000Z.json").exists()


def test_controlled_trial_report_preserves_replayable_visible_text() -> None:
    trial = _trial("briefing")

    serialized = _redact_trial(trial)

    assert serialized["visible_text"] == trial["visible_text"]
    assert serialized["visible_content_length"] == len(trial["visible_text"])
    assert serialized["visible_content_hash"].startswith("sha256:")
