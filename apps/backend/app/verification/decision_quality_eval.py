"""Deterministic decision-quality grading and model-promotion gates.

The evaluator intentionally separates controlled engineering contracts from a
domain-accuracy claim. It grades observable answer, citation, scope, quality,
guardrail, latency, and cost fields; model-provided self-scores are never used as
ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_ALLOWED_REVIEW_STATUSES = {"draft", "engineering_reviewed", "owner_reviewed"}
_ALLOWED_BEHAVIORS = {"answer", "abstain", "present_conflict"}
_CITATION_RE = re.compile(r"\[(\d+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;\n])")
_QUALITY_METRICS = (
    "trial_pass_rate",
    "critical_trial_pass_rate",
    "fact_recall",
    "citation_precision",
    "scope_accuracy",
    "reference_time_accuracy",
    "abstention_accuracy",
)


def default_suite_path() -> Path:
    return Path(__file__).resolve().parents[4] / "evals" / "decision-quality" / "suite.json"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_strings(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item or "").strip()]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _contains(text: str, term: str) -> bool:
    return bool(_normalized(term)) and _normalized(term) in _normalized(text)


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _suite_hash(suite: Mapping[str, Any]) -> str:
    canonical = json.dumps(suite, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_suite(path: str | Path | None = None) -> dict[str, Any]:
    resolved = Path(path) if path else default_suite_path()
    return json.loads(resolved.read_text(encoding="utf-8"))


def _case_validation_errors(case: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = str(case.get("id") or "<missing-id>").strip()
    category = str(case.get("category") or "").strip()
    expected = _as_mapping(case.get("expected"))
    fixture = _as_mapping(case.get("fixture"))
    sources = [item for item in _as_list(fixture.get("sources")) if isinstance(item, Mapping)]
    source_ids = _dedupe(str(item.get("source_id") or "").strip() for item in sources)

    if case_id == "<missing-id>":
        errors.append("case missing id")
    if not category:
        errors.append(f"{case_id}: category required")
    if not isinstance(case.get("critical"), bool):
        errors.append(f"{case_id}: critical must be boolean")
    if not str(case.get("question") or "").strip():
        errors.append(f"{case_id}: question required")
    if not sources:
        errors.append(f"{case_id}: fixture.sources required")
    if len(source_ids) != len(sources):
        errors.append(f"{case_id}: source ids must be unique and non-empty")

    behavior = str(expected.get("behavior") or "").strip()
    if behavior not in _ALLOWED_BEHAVIORS:
        errors.append(f"{case_id}: invalid expected.behavior")
    expected_scope = _as_strings(expected.get("scope_source_ids"))
    if not expected_scope:
        errors.append(f"{case_id}: expected.scope_source_ids required")
    elif set(expected_scope) != set(source_ids):
        errors.append(f"{case_id}: expected scope must exactly match fixture sources")
    if not _as_strings(expected.get("reference_time_any")):
        errors.append(f"{case_id}: expected.reference_time_any required")

    fact_units = [item for item in _as_list(expected.get("required_fact_units")) if isinstance(item, Mapping)]
    if behavior in {"answer", "present_conflict"} and not fact_units:
        errors.append(f"{case_id}: answer/conflict cases require fact units")
    fact_ids: list[str] = []
    for fact in fact_units:
        fact_id = str(fact.get("id") or "").strip()
        fact_ids.append(fact_id)
        if not fact_id:
            errors.append(f"{case_id}: fact id required")
        if not _as_strings(fact.get("all_terms")):
            errors.append(f"{case_id}:{fact_id or '<missing-fact>'}: all_terms required")
        citation_sources = _as_strings(fact.get("citation_source_ids"))
        if not citation_sources:
            errors.append(f"{case_id}:{fact_id or '<missing-fact>'}: citation_source_ids required")
        elif not set(citation_sources).issubset(set(expected_scope)):
            errors.append(f"{case_id}:{fact_id or '<missing-fact>'}: citation sources outside scope")
    if len(fact_ids) != len(set(fact_ids)):
        errors.append(f"{case_id}: fact ids must be unique")

    if behavior == "abstain" and expected.get("decision_ready") is not False:
        errors.append(f"{case_id}: abstain case must require decision_ready=false")
    if not _as_strings(expected.get("guardrail_statuses")):
        errors.append(f"{case_id}: guardrail_statuses required")
    if not _as_strings(expected.get("effective_actions")):
        errors.append(f"{case_id}: effective_actions required")
    return errors


def validate_suite(suite: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    suite_id = str(suite.get("suite_id") or "").strip()
    version = str(suite.get("version") or "").strip()
    review = _as_mapping(suite.get("review"))
    review_status = str(review.get("status") or "").strip()
    thresholds = _as_mapping(suite.get("thresholds"))
    cases = [item for item in _as_list(suite.get("cases")) if isinstance(item, Mapping)]

    if not suite_id:
        errors.append("suite_id required")
    if not version:
        errors.append("version required")
    if review_status not in _ALLOWED_REVIEW_STATUSES:
        errors.append("review.status invalid")
    if not isinstance(suite.get("domain_accuracy_claim_allowed"), bool):
        errors.append("domain_accuracy_claim_allowed must be boolean")
    if not cases:
        errors.append("at least one case required")

    case_ids = [str(case.get("id") or "").strip() for case in cases]
    duplicate_case_ids = sorted({case_id for case_id in case_ids if case_id and case_ids.count(case_id) > 1})
    if duplicate_case_ids:
        errors.append(f"duplicate case ids: {', '.join(duplicate_case_ids)}")

    required_categories = set(_as_strings(suite.get("required_categories")))
    actual_categories = {str(case.get("category") or "").strip() for case in cases}
    missing_categories = sorted(required_categories - actual_categories)
    if missing_categories:
        errors.append(f"missing required categories: {', '.join(missing_categories)}")
    for case in cases:
        errors.extend(_case_validation_errors(case))

    threshold_rules = {
        "min_trials_per_case": lambda value: value >= 1 and float(value).is_integer(),
        "min_trial_pass_rate": lambda value: 0 <= value <= 1,
        "min_critical_trial_pass_rate": lambda value: 0 <= value <= 1,
        "min_fact_recall": lambda value: 0 <= value <= 1,
        "min_citation_precision": lambda value: 0 <= value <= 1,
        "min_scope_accuracy": lambda value: 0 <= value <= 1,
        "min_reference_time_accuracy": lambda value: 0 <= value <= 1,
        "min_abstention_accuracy": lambda value: 0 <= value <= 1,
        "max_unsupported_claims": lambda value: value >= 0 and float(value).is_integer(),
        "candidate_max_quality_regression": lambda value: 0 <= value <= 1,
        "candidate_max_p95_latency_ratio": lambda value: value > 0,
        "candidate_max_p95_cost_ratio": lambda value: value > 0,
    }
    for key, rule in threshold_rules.items():
        raw = thresholds.get(key)
        value = _number(raw, float("nan"))
        if not math.isfinite(value) or not rule(value):
            errors.append(f"thresholds.{key} invalid")

    promotion_ready = (
        review_status == "owner_reviewed"
        and suite.get("domain_accuracy_claim_allowed") is True
        and not errors
    )
    if not promotion_ready:
        warnings.append("product_owner_review_required_before_model_promotion")

    return {
        "report_type": "decision_quality_suite_validation",
        "status": "passed" if not errors else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": suite_id or None,
        "version": version or None,
        "suite_hash": _suite_hash(suite),
        "case_count": len(cases),
        "critical_case_count": sum(1 for case in cases if case.get("critical") is True),
        "categories": sorted(actual_categories - {""}),
        "missing_categories": missing_categories,
        "review_status": review_status or None,
        "domain_accuracy_claim_allowed": suite.get("domain_accuracy_claim_allowed") is True,
        "promotion_ready": promotion_ready,
        "errors": errors,
        "warnings": warnings,
    }


def _case_map(suite: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(case.get("id")): case
        for case in _as_list(suite.get("cases"))
        if isinstance(case, Mapping) and str(case.get("id") or "").strip()
    }


def _sentences(answer: str) -> list[str]:
    return [item.strip() for item in _SENTENCE_SPLIT_RE.split(answer or "") if item.strip()]


def _citation_map(trial: Mapping[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    for position, item in enumerate(_as_list(trial.get("citations")), start=1):
        if not isinstance(item, Mapping):
            continue
        try:
            index = int(item.get("index") or position)
        except (TypeError, ValueError):
            continue
        source_id = str(item.get("source_id") or "").strip()
        if source_id:
            result[index] = source_id
    return result


def _fact_sentence(answer: str, fact: Mapping[str, Any]) -> str | None:
    terms = _as_strings(fact.get("all_terms"))
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", answer or "") if item.strip()]
    for paragraph in paragraphs:
        sentences = _sentences(paragraph)
        for sentence in sentences:
            if all(_contains(sentence, term) for term in terms):
                return sentence
        for index, sentence in enumerate(sentences):
            if index == 0 or not _CITATION_RE.search(sentence):
                continue
            current_term_count = sum(1 for term in terms if _contains(sentence, term))
            if current_term_count < max(1, len(terms) - 1):
                continue
            local_context = f"{sentences[index - 1]} {sentence}"
            if all(_contains(local_context, term) for term in terms):
                return sentence
    return None


def _unsupported_claim_count(trial: Mapping[str, Any]) -> int:
    quality = _as_mapping(trial.get("quality_report"))
    evidence = _as_mapping(quality.get("evidence"))
    keys = (
        "uncited_material_claim_count",
        "low_overlap_citation_count",
        "direction_conflict_citation_count",
        "invalid_citation_count",
    )
    return sum(max(0, int(_number(evidence.get(key), 0))) for key in keys)


def _trace_cost(trial: Mapping[str, Any]) -> float:
    trace = _as_mapping(trial.get("llm_trace"))
    direct = trace.get("total_cost_usd")
    if direct is not None:
        return max(0.0, _number(direct))
    return sum(
        max(0.0, _number(item.get("cost_usd")))
        for item in _as_list(trace.get("calls"))
        if isinstance(item, Mapping)
    )


def _trace_is_real(trial: Mapping[str, Any]) -> bool:
    trace = _as_mapping(trial.get("llm_trace"))
    if not trace or trace.get("mock") is True:
        return False
    calls = [item for item in _as_list(trace.get("calls")) if isinstance(item, Mapping)]
    return not any(item.get("mock") is True for item in calls)


def _timing_total_ms(trial: Mapping[str, Any]) -> int:
    timing = _as_mapping(trial.get("timing_trace"))
    value = timing.get("total_ms", trial.get("duration_ms", 0))
    return max(0, int(_number(value, 0)))


def _insufficient_reason(trial: Mapping[str, Any]) -> str:
    quality = _as_mapping(trial.get("quality_report"))
    filters = _as_mapping(trial.get("quality_filters"))
    return str(
        quality.get("insufficient_evidence_reason")
        or filters.get("insufficient_evidence_reason")
        or ""
    ).strip()


def grade_trial(suite: Mapping[str, Any], trial: Mapping[str, Any]) -> dict[str, Any]:
    case_id = str(trial.get("case_id") or "").strip()
    case = _case_map(suite).get(case_id)
    if case is None:
        return {
            "status": "failed",
            "case_id": case_id or None,
            "candidate_id": str(trial.get("candidate_id") or "").strip() or None,
            "trial_id": str(trial.get("trial_id") or "").strip() or None,
            "critical": True,
            "failures": ["unknown_case_id"],
            "checks": {},
            "metrics": {},
        }

    expected = _as_mapping(case.get("expected"))
    answer = str(trial.get("answer") or "").strip()
    citation_by_index = _citation_map(trial)
    used_indices = [int(value) for value in _CITATION_RE.findall(answer)]
    allowed_scope = set(_as_strings(expected.get("scope_source_ids")))
    used_sources = [citation_by_index.get(index) for index in used_indices]
    valid_used_sources = [source_id for source_id in used_sources if source_id]
    valid_allowed_uses = sum(1 for source_id in valid_used_sources if source_id in allowed_scope)
    citation_precision = (
        valid_allowed_uses / len(used_indices)
        if used_indices
        else 1.0
    )

    fact_units = [item for item in _as_list(expected.get("required_fact_units")) if isinstance(item, Mapping)]
    fact_results: list[dict[str, Any]] = []
    failures: list[str] = []
    for fact in fact_units:
        fact_id = str(fact.get("id") or "").strip()
        sentence = _fact_sentence(answer, fact)
        present = sentence is not None
        sentence_indices = [int(value) for value in _CITATION_RE.findall(sentence or "")]
        expected_sources = set(_as_strings(fact.get("citation_source_ids")))
        cited = present and any(citation_by_index.get(index) in expected_sources for index in sentence_indices)
        fact_results.append({
            "fact_id": fact_id,
            "present": present,
            "cited": cited,
            "sentence": sentence,
            "citation_indices": sentence_indices,
        })
        if not present:
            failures.append(f"required_fact_missing:{fact_id}")
        elif not cited:
            failures.append(f"required_fact_citation_missing:{fact_id}")

    fact_recall = sum(1 for item in fact_results if item["present"]) / len(fact_results) if fact_results else 1.0
    fact_citation_coverage = sum(1 for item in fact_results if item["cited"]) / len(fact_results) if fact_results else 1.0

    first_lines = "\n".join([line for line in answer.splitlines() if line.strip()][:3])
    quality = _as_mapping(trial.get("quality_report"))
    system_reference_time = str(quality.get("reference_time") or "").strip()
    reference_time_ok = bool(system_reference_time) and _contains(first_lines, system_reference_time)
    if not reference_time_ok:
        failures.append("reference_time_missing_or_wrong")
    requested_time_context_ok = any(
        _contains(answer, term)
        for term in _as_strings(expected.get("reference_time_any"))
    )
    if not requested_time_context_ok:
        failures.append("requested_time_context_missing")

    scope = _as_mapping(trial.get("scope"))
    actual_scope = set(_as_strings(scope.get("source_ids")))
    scope_exact = str(scope.get("mode") or "") == "selected_sources" and actual_scope == allowed_scope
    if not scope_exact:
        failures.append("source_scope_mismatch")

    required_term_groups = [
        _as_strings(group)
        for group in _as_list(expected.get("required_answer_term_groups"))
        if isinstance(group, list)
    ]
    missing_groups = [group for group in required_term_groups if not any(_contains(answer, term) for term in group)]
    if missing_groups:
        failures.append("required_answer_signal_missing")

    forbidden_hits = [term for term in _as_strings(expected.get("forbidden_terms")) if _contains(answer, term)]
    if forbidden_hits:
        failures.append("forbidden_answer_content")

    decision_ready_expected = expected.get("decision_ready")
    decision_ready_ok = isinstance(decision_ready_expected, bool) and quality.get("decision_ready") is decision_ready_expected
    if not decision_ready_ok:
        failures.append("decision_ready_mismatch")

    guardrail = _as_mapping(trial.get("decision_guardrail"))
    guardrail_status = str(guardrail.get("status") or "").strip()
    guardrail_status_ok = guardrail_status in set(_as_strings(expected.get("guardrail_statuses")))
    if not guardrail_status_ok:
        failures.append("guardrail_status_mismatch")
    effective_action = str(guardrail.get("effective_action") or "").strip()
    expected_actions = set(_as_strings(expected.get("effective_actions")))
    effective_action_ok = effective_action in expected_actions or (
        not effective_action
        and "watch" in expected_actions
        and guardrail.get("actionable_language_detected") is False
    )
    if not effective_action_ok:
        failures.append("effective_action_mismatch")

    expected_reasons = set(_as_strings(expected.get("insufficient_evidence_reasons")))
    reason = _insufficient_reason(trial)
    insufficient_reason_ok = not expected_reasons or reason in expected_reasons
    if not insufficient_reason_ok:
        failures.append("insufficient_evidence_reason_mismatch")

    unsupported_claim_count = _unsupported_claim_count(trial)
    if unsupported_claim_count:
        failures.append("unsupported_material_claims")
    real_trace = _trace_is_real(trial)
    if not real_trace:
        failures.append("real_model_trace_missing")

    behavior = str(expected.get("behavior") or "")
    abstention_accuracy = 1.0
    if behavior == "abstain":
        abstention_accuracy = 1.0 if (
            decision_ready_ok
            and guardrail_status_ok
            and effective_action_ok
            and insufficient_reason_ok
            and not forbidden_hits
        ) else 0.0

    checks = {
        "required_facts": fact_recall == 1.0,
        "fact_citations": fact_citation_coverage == 1.0,
        "citation_precision": citation_precision == 1.0,
        "reference_time": reference_time_ok,
        "requested_time_context": requested_time_context_ok,
        "scope_exact": scope_exact,
        "required_answer_signals": not missing_groups,
        "forbidden_terms": not forbidden_hits,
        "decision_ready": decision_ready_ok,
        "guardrail_status": guardrail_status_ok,
        "effective_action": effective_action_ok,
        "insufficient_evidence_reason": insufficient_reason_ok,
        "unsupported_claims": unsupported_claim_count == 0,
        "real_model_trace": real_trace,
    }
    return {
        "status": "passed" if not failures else "failed",
        "case_id": case_id,
        "category": case.get("category"),
        "critical": case.get("critical") is True,
        "candidate_id": str(trial.get("candidate_id") or "").strip() or None,
        "trial_id": str(trial.get("trial_id") or "").strip() or None,
        "behavior": behavior,
        "checks": checks,
        "metrics": {
            "fact_recall": round(fact_recall, 6),
            "fact_citation_coverage": round(fact_citation_coverage, 6),
            "citation_precision": round(citation_precision, 6),
            "scope_accuracy": 1.0 if scope_exact else 0.0,
            "reference_time_accuracy": 1.0 if reference_time_ok and requested_time_context_ok else 0.0,
            "abstention_accuracy": abstention_accuracy,
            "unsupported_claim_count": unsupported_claim_count,
            "total_ms": _timing_total_ms(trial),
            "cost_usd": round(_trace_cost(trial), 8),
        },
        "fact_results": fact_results,
        "used_citation_indices": used_indices,
        "used_citation_source_ids": valid_used_sources,
        "forbidden_hits": forbidden_hits,
        "insufficient_evidence_reason": reason or None,
        "failures": _dedupe(failures),
    }


def _average(values: Sequence[float], default: float = 1.0) -> float:
    return sum(values) / len(values) if values else default


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _aggregate_candidate(
    suite: Mapping[str, Any],
    candidate_id: str,
    grades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    cases = _case_map(suite)
    thresholds = _as_mapping(suite.get("thresholds"))
    minimum_trials = int(_number(thresholds.get("min_trials_per_case"), 1))
    counts: dict[str, int] = defaultdict(int)
    passed_counts: dict[str, int] = defaultdict(int)
    for grade in grades:
        case_id = str(grade.get("case_id") or "")
        counts[case_id] += 1
        if grade.get("status") == "passed":
            passed_counts[case_id] += 1
    missing_by_case = {
        case_id: max(0, minimum_trials - counts.get(case_id, 0))
        for case_id in cases
    }
    missing_trial_count = sum(missing_by_case.values())
    case_pass_rates = {
        case_id: (passed_counts.get(case_id, 0) / count if count else 0.0)
        for case_id, count in counts.items()
        if case_id in cases
    }
    trial_count = len(grades)
    passed_trials = sum(1 for grade in grades if grade.get("status") == "passed")
    critical_grades = [grade for grade in grades if grade.get("critical") is True]
    critical_passed = sum(1 for grade in critical_grades if grade.get("status") == "passed")
    metric_rows = [_as_mapping(grade.get("metrics")) for grade in grades]
    abstention_rows = [
        _as_mapping(grade.get("metrics"))
        for grade in grades
        if grade.get("behavior") == "abstain"
    ]
    metrics = {
        "trial_pass_rate": passed_trials / trial_count if trial_count else 0.0,
        "critical_trial_pass_rate": critical_passed / len(critical_grades) if critical_grades else 1.0,
        "fact_recall": _average([_number(row.get("fact_recall")) for row in metric_rows]),
        "citation_precision": _average([_number(row.get("citation_precision")) for row in metric_rows]),
        "scope_accuracy": _average([_number(row.get("scope_accuracy")) for row in metric_rows]),
        "reference_time_accuracy": _average([_number(row.get("reference_time_accuracy")) for row in metric_rows]),
        "abstention_accuracy": _average([_number(row.get("abstention_accuracy")) for row in abstention_rows]),
        "unsupported_claim_count": sum(int(_number(row.get("unsupported_claim_count"))) for row in metric_rows),
        "p95_total_ms": int(_p95([_number(row.get("total_ms")) for row in metric_rows])),
        "p95_cost_usd": round(_p95([_number(row.get("cost_usd")) for row in metric_rows]), 8),
    }
    metric_checks = {
        "trial_pass_rate": metrics["trial_pass_rate"] >= _number(thresholds.get("min_trial_pass_rate"), 1),
        "critical_trial_pass_rate": metrics["critical_trial_pass_rate"] >= _number(thresholds.get("min_critical_trial_pass_rate"), 1),
        "fact_recall": metrics["fact_recall"] >= _number(thresholds.get("min_fact_recall"), 1),
        "citation_precision": metrics["citation_precision"] >= _number(thresholds.get("min_citation_precision"), 1),
        "scope_accuracy": metrics["scope_accuracy"] >= _number(thresholds.get("min_scope_accuracy"), 1),
        "reference_time_accuracy": metrics["reference_time_accuracy"] >= _number(thresholds.get("min_reference_time_accuracy"), 1),
        "abstention_accuracy": metrics["abstention_accuracy"] >= _number(thresholds.get("min_abstention_accuracy"), 1),
        "unsupported_claim_count": metrics["unsupported_claim_count"] <= int(_number(thresholds.get("max_unsupported_claims"), 0)),
        "minimum_trials": missing_trial_count == 0,
    }
    return {
        "status": "passed" if all(metric_checks.values()) else "failed",
        "candidate_id": candidate_id,
        "trial_count": trial_count,
        "passed_trial_count": passed_trials,
        "missing_trial_count": missing_trial_count,
        "missing_trials_by_case": missing_by_case,
        "case_pass_rates": case_pass_rates,
        "metrics": metrics,
        "checks": metric_checks,
        "trials": list(grades),
    }


def evaluate_suite(suite: Mapping[str, Any], trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    validation = validate_suite(suite)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        candidate_id = str(trial.get("candidate_id") or "").strip() or "<missing-candidate>"
        grouped[candidate_id].append(grade_trial(suite, trial))
    candidates = {
        candidate_id: _aggregate_candidate(suite, candidate_id, grades)
        for candidate_id, grades in grouped.items()
    }
    status = "failed" if validation["status"] != "passed" or not candidates else (
        "passed" if all(item["status"] == "passed" for item in candidates.values()) else "failed"
    )
    return {
        "report_type": "decision_quality_evaluation",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": validation.get("suite_id"),
        "suite_version": validation.get("version"),
        "suite_hash": validation.get("suite_hash"),
        "suite_validation": validation,
        "trial_count": len(trials),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def compare_candidate_reports(
    suite: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    review = _as_mapping(suite.get("review"))
    thresholds = _as_mapping(suite.get("thresholds"))
    baseline_metrics = _as_mapping(baseline.get("metrics"))
    candidate_metrics = _as_mapping(candidate.get("metrics"))
    allowed_regression = _number(thresholds.get("candidate_max_quality_regression"), 0)
    baseline_case_rates = _as_mapping(baseline.get("case_pass_rates"))
    candidate_case_rates = _as_mapping(candidate.get("case_pass_rates"))
    no_case_regression = all(
        _number(candidate_case_rates.get(case_id), 0) + allowed_regression >= _number(rate, 0)
        for case_id, rate in baseline_case_rates.items()
    )

    baseline_latency = _number(baseline_metrics.get("p95_total_ms"), 0)
    candidate_latency = _number(candidate_metrics.get("p95_total_ms"), 0)
    latency_ratio = candidate_latency / baseline_latency if baseline_latency > 0 else (1.0 if candidate_latency <= 0 else math.inf)
    baseline_cost = _number(baseline_metrics.get("p95_cost_usd"), 0)
    candidate_cost = _number(candidate_metrics.get("p95_cost_usd"), 0)
    cost_ratio = candidate_cost / baseline_cost if baseline_cost > 0 else (1.0 if candidate_cost <= 0 else math.inf)

    checks = {
        "suite_owner_reviewed": review.get("status") == "owner_reviewed" and suite.get("domain_accuracy_claim_allowed") is True,
        "baseline_passed": baseline.get("status") == "passed",
        "candidate_passed": candidate.get("status") == "passed",
        "no_case_regression": no_case_regression,
        "trial_pass_rate_non_regression": _number(candidate_metrics.get("trial_pass_rate")) + allowed_regression >= _number(baseline_metrics.get("trial_pass_rate")),
        "critical_trial_pass_rate_non_regression": _number(candidate_metrics.get("critical_trial_pass_rate")) + allowed_regression >= _number(baseline_metrics.get("critical_trial_pass_rate")),
        "fact_recall_non_regression": _number(candidate_metrics.get("fact_recall")) + allowed_regression >= _number(baseline_metrics.get("fact_recall")),
        "citation_precision_non_regression": _number(candidate_metrics.get("citation_precision")) + allowed_regression >= _number(baseline_metrics.get("citation_precision")),
        "scope_accuracy_non_regression": _number(candidate_metrics.get("scope_accuracy")) + allowed_regression >= _number(baseline_metrics.get("scope_accuracy")),
        "reference_time_accuracy_non_regression": _number(candidate_metrics.get("reference_time_accuracy")) + allowed_regression >= _number(baseline_metrics.get("reference_time_accuracy")),
        "abstention_accuracy_non_regression": _number(candidate_metrics.get("abstention_accuracy")) + allowed_regression >= _number(baseline_metrics.get("abstention_accuracy")),
        "unsupported_claims_zero": int(_number(candidate_metrics.get("unsupported_claim_count"))) <= int(_number(thresholds.get("max_unsupported_claims"), 0)),
        "p95_latency_within_limit": latency_ratio <= _number(thresholds.get("candidate_max_p95_latency_ratio"), 1.25),
        "p95_cost_within_limit": cost_ratio <= _number(thresholds.get("candidate_max_p95_cost_ratio"), 1.25),
    }
    if not checks["suite_owner_reviewed"]:
        status = "blocked"
    else:
        status = "promoted" if all(checks.values()) else "rejected"
    return {
        "report_type": "decision_quality_candidate_comparison",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": suite.get("suite_id"),
        "suite_version": suite.get("version"),
        "suite_hash": _suite_hash(suite),
        "checks": checks,
        "quality_regression_tolerance": allowed_regression,
        "p95_latency_ratio": None if not math.isfinite(latency_ratio) else round(latency_ratio, 6),
        "p95_cost_ratio": None if not math.isfinite(cost_ratio) else round(cost_ratio, 6),
    }


def _load_trials(path: str | Path) -> list[Mapping[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping) and isinstance(payload.get("trials"), list):
        return [item for item in payload["trials"] if isinstance(item, Mapping)]
    raise ValueError("trial file must be a list or an object containing trials")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate or execute deterministic decision-quality gates.")
    parser.add_argument("--suite", default=str(default_suite_path()))
    parser.add_argument("--trials")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--report")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args(argv)

    suite = load_suite(args.suite)
    if args.validate_only or not args.trials:
        report: dict[str, Any] = validate_suite(suite)
    else:
        report = evaluate_suite(suite, _load_trials(args.trials))
        if args.baseline and args.candidate:
            candidates = _as_mapping(report.get("candidates"))
            baseline = _as_mapping(candidates.get(args.baseline))
            candidate = _as_mapping(candidates.get(args.candidate))
            report["promotion"] = compare_candidate_reports(suite, baseline, candidate)

    report_path = (
        Path(args.report)
        if args.report
        else Path(__file__).resolve().parents[4] / "docs" / "verification" / "decision-quality-eval-latest.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = report.get("status") not in {"passed", "promoted"}
    return 1 if args.fail_on_error and failed else 0


__all__ = [
    "compare_candidate_reports",
    "default_suite_path",
    "evaluate_suite",
    "grade_trial",
    "load_suite",
    "validate_suite",
]


if __name__ == "__main__":
    sys.exit(main())
