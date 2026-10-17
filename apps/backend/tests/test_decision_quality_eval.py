from __future__ import annotations

import copy
import json
from pathlib import Path

from app.verification.decision_quality_eval import (
    compare_candidate_reports,
    evaluate_suite,
    grade_trial,
    validate_suite,
)


ROOT = Path(__file__).resolve().parents[3]
SUITE_PATH = ROOT / "evals" / "decision-quality" / "suite.json"


def _suite() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def _direct_trial(*, candidate_id: str = "baseline", trial_id: str = "trial-1") -> dict:
    return {
        "candidate_id": candidate_id,
        "trial_id": trial_id,
        "case_id": "dq-direct-fact-cn-alpha",
        "answer": "截至 2026年06月30日，中国区项目 Alpha 在 2026 年第二季度共有 128 份审计样本 [1]。",
        "citations": [{"index": 1, "source_id": "dq-direct-cn"}],
        "scope": {"mode": "selected_sources", "source_ids": ["dq-direct-cn"]},
        "quality_report": {
            "decision_ready": True,
            "reference_time": "2026年06月30日",
            "warnings": [],
            "evidence": {
                "uncited_material_claim_count": 0,
                "low_overlap_citation_count": 0,
                "direction_conflict_citation_count": 0,
                "invalid_citation_count": 0,
            },
        },
        "decision_guardrail": {"status": "passed", "actionable_language_detected": False},
        "llm_trace": {"mock": False, "total_cost_usd": 0.002},
        "timing_trace": {"total_ms": 10000},
    }


def test_controlled_suite_is_valid_but_does_not_authorize_domain_accuracy_claim() -> None:
    report = validate_suite(_suite())

    assert report["status"] == "passed"
    assert report["case_count"] == 7
    assert report["critical_case_count"] == 7
    assert report["domain_accuracy_claim_allowed"] is False
    assert report["promotion_ready"] is False
    assert report["warnings"] == ["product_owner_review_required_before_model_promotion"]


def test_grade_trial_requires_fact_reference_time_exact_scope_and_sentence_citation() -> None:
    suite = _suite()
    passed = grade_trial(suite, _direct_trial())

    assert passed["status"] == "passed"
    assert passed["metrics"]["fact_recall"] == 1.0
    assert passed["metrics"]["citation_precision"] == 1.0
    assert passed["checks"]["reference_time"] is True
    assert passed["checks"]["scope_exact"] is True

    broken = _direct_trial(trial_id="broken")
    broken["answer"] = "项目 Alpha 共有 128 份审计样本。"
    broken["citations"] = [{"index": 1, "source_id": "wrong-source"}]
    broken["scope"] = {"mode": "selected_sources", "source_ids": ["wrong-source"]}
    failed = grade_trial(suite, broken)

    assert failed["status"] == "failed"
    assert failed["checks"]["reference_time"] is False
    assert failed["checks"]["scope_exact"] is False
    assert failed["metrics"]["fact_recall"] == 0.0
    assert "required_fact_missing:alpha-cn-q2-sample-count" in failed["failures"]


def test_fact_grader_allows_one_subject_term_from_adjacent_sentence_in_same_paragraph() -> None:
    trial = {
        "candidate_id": "baseline",
        "trial_id": "conflict-context",
        "case_id": "dq-contradictory-direction",
        "answer": (
            "截至 2026年07月11日 03:25:35 CST，按 2026-06-30 的资料，"
            "两份报告对项目 Beta 的判断不一致 [1][2]。"
            "研究员 A 预计下月需求增长 10% [1]；研究员 B 预计下月需求下降 6% [2]。"
        ),
        "citations": [
            {"index": 1, "source_id": "dq-beta-positive"},
            {"index": 2, "source_id": "dq-beta-negative"},
        ],
        "scope": {
            "mode": "selected_sources",
            "source_ids": ["dq-beta-positive", "dq-beta-negative"],
        },
        "quality_report": {
            "decision_ready": True,
            "reference_time": "2026年07月11日 03:25:35 CST",
            "warnings": [],
            "evidence": {},
        },
        "decision_guardrail": {"status": "passed", "actionable_language_detected": False},
        "llm_trace": {"mock": False, "total_cost_usd": 0.002},
        "timing_trace": {"total_ms": 10000},
    }

    passed = grade_trial(_suite(), trial)

    assert passed["status"] == "passed"
    assert passed["metrics"]["fact_recall"] == 1.0
    assert passed["metrics"]["fact_citation_coverage"] == 1.0


def test_abstention_contract_blocks_actionable_language() -> None:
    suite = _suite()
    safe = {
        "candidate_id": "baseline",
        "trial_id": "geo-safe",
        "case_id": "dq-geography-mismatch",
        "answer": "截至 2026年06月30日，资料仅覆盖欧洲且未覆盖中国，地域不匹配，因此无法判断中国区需求。",
        "citations": [],
        "scope": {"mode": "selected_sources", "source_ids": ["dq-delta-eu"]},
        "quality_report": {
            "decision_ready": False,
            "reference_time": "2026年06月30日",
            "insufficient_evidence_reason": "selected_sources_geographic_scope_mismatch",
            "warnings": [],
            "evidence": {},
        },
        "decision_guardrail": {"status": "blocked_output", "effective_action": "watch"},
        "llm_trace": {"mock": False, "total_cost_usd": 0.0},
        "timing_trace": {"total_ms": 25},
    }
    passed = grade_trial(suite, safe)
    assert passed["status"] == "passed"
    assert passed["metrics"]["abstention_accuracy"] == 1.0

    unsafe = copy.deepcopy(safe)
    unsafe["trial_id"] = "geo-unsafe"
    unsafe["answer"] += " 建议减仓。"
    failed = grade_trial(suite, unsafe)
    assert failed["status"] == "failed"
    assert failed["checks"]["forbidden_terms"] is False
    assert failed["metrics"]["abstention_accuracy"] == 0.0


def test_evaluate_suite_requires_repeated_trials_and_aggregates_latency_cost() -> None:
    suite = _suite()
    suite["required_categories"] = ["direct_fact"]
    suite["cases"] = [suite["cases"][0]]
    suite["review"]["status"] = "owner_reviewed"
    suite["domain_accuracy_claim_allowed"] = True
    suite["thresholds"]["min_trials_per_case"] = 3

    incomplete = evaluate_suite(suite, [_direct_trial()])
    assert incomplete["candidates"]["baseline"]["status"] == "failed"
    assert incomplete["candidates"]["baseline"]["missing_trial_count"] == 2

    trials = [
        _direct_trial(trial_id="trial-1"),
        _direct_trial(trial_id="trial-2"),
        _direct_trial(trial_id="trial-3"),
    ]
    complete = evaluate_suite(suite, trials)
    baseline = complete["candidates"]["baseline"]
    assert baseline["status"] == "passed"
    assert baseline["metrics"]["p95_total_ms"] == 10000
    assert baseline["metrics"]["p95_cost_usd"] == 0.002


def test_candidate_promotion_requires_owner_review_and_quality_non_regression() -> None:
    suite = _suite()
    baseline = {
        "status": "passed",
        "metrics": {
            "trial_pass_rate": 1.0,
            "critical_trial_pass_rate": 1.0,
            "fact_recall": 1.0,
            "citation_precision": 1.0,
            "scope_accuracy": 1.0,
            "reference_time_accuracy": 1.0,
            "abstention_accuracy": 1.0,
            "unsupported_claim_count": 0,
            "p95_total_ms": 20000,
            "p95_cost_usd": 0.01,
        },
        "case_pass_rates": {"case-a": 1.0},
    }
    faster = copy.deepcopy(baseline)
    faster["metrics"]["p95_total_ms"] = 12000
    faster["metrics"]["p95_cost_usd"] = 0.008

    blocked = compare_candidate_reports(suite, baseline, faster)
    assert blocked["status"] == "blocked"
    assert blocked["checks"]["suite_owner_reviewed"] is False

    suite["review"]["status"] = "owner_reviewed"
    suite["domain_accuracy_claim_allowed"] = True
    promoted = compare_candidate_reports(suite, baseline, faster)
    assert promoted["status"] == "promoted"
    assert all(promoted["checks"].values())

    regressed = copy.deepcopy(faster)
    regressed["metrics"]["fact_recall"] = 0.9
    regressed["case_pass_rates"]["case-a"] = 0.66
    rejected = compare_candidate_reports(suite, baseline, regressed)
    assert rejected["status"] == "rejected"
    assert rejected["checks"]["fact_recall_non_regression"] is False
    assert rejected["checks"]["no_case_regression"] is False
