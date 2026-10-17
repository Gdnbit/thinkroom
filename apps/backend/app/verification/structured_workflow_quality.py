"""Deterministic evaluator for repeated briefing and committee quality trials."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_WORKFLOWS = {"briefing", "committee"}
_BEHAVIORS = {"answer", "present_conflict", "abstain"}
_MODEL_CALL_POLICIES = {"required", "forbidden"}
_SAFE_FIELD_STATUSES = {
    "supported",
    "inference_with_evidence",
    "inference_input_basis",
    "scope_assessment",
    "model_derived_assessment",
    "policy_controlled",
    "not_applicable",
    "insufficient",
}
_MATERIAL_FIELDS = {
    "briefing": {
        "direction",
        "recommended_action",
        "executive_summary",
        "supporting_points",
        "contrary_points",
        "key_risks",
    },
    "committee": {
        "direction",
        "recommended_action",
        "synthesis",
        "risk_warnings",
        "structure_warnings",
    },
}


def default_suite_path() -> Path:
    return Path(__file__).resolve().parents[4] / "evals" / "structured-workflow-quality" / "suite.json"


def load_suite(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or default_suite_path()).read_text(encoding="utf-8"))


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item or "").strip()]


def _term_groups(value: Any) -> list[list[str]]:
    return [
        terms
        for item in _as_list(value)
        if (terms := _strings(item))
    ]


def _normalized(value: Any) -> str:
    return "".join(str(value or "").casefold().split())


def _contains(text: str, term: str) -> bool:
    return _normalized(term) in _normalized(text)


def _suite_hash(suite: Mapping[str, Any]) -> str:
    encoded = json.dumps(suite, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_suite(suite: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    workflows = _strings(suite.get("workflows"))
    route_scopes = _mapping(suite.get("route_scopes"))
    thresholds = _mapping(suite.get("thresholds"))
    cases = [item for item in _as_list(suite.get("cases")) if isinstance(item, Mapping)]
    if not str(suite.get("suite_id") or "").strip():
        errors.append("suite_id required")
    if not str(suite.get("version") or "").strip():
        errors.append("version required")
    if set(workflows) != _WORKFLOWS:
        errors.append("workflows must contain briefing and committee")
    for workflow in workflows:
        if not str(route_scopes.get(workflow) or "").strip():
            errors.append(f"route_scopes.{workflow} required")
    required_thresholds = {
        "min_trials_per_case",
        "min_trial_pass_rate",
        "min_fact_recall",
        "min_scope_accuracy",
        "max_unverified_material_fields",
    }
    for name in required_thresholds:
        if not isinstance(thresholds.get(name), (int, float)):
            errors.append(f"thresholds.{name} invalid")
    case_ids: list[str] = []
    for case in cases:
        case_id = str(case.get("id") or "").strip()
        case_ids.append(case_id)
        expected = _mapping(case.get("expected"))
        sources = [item for item in _as_list(_mapping(case.get("fixture")).get("sources")) if isinstance(item, Mapping)]
        source_ids = [str(item.get("source_id") or "").strip() for item in sources]
        if not case_id:
            errors.append("case id required")
        if str(expected.get("behavior") or "") not in _BEHAVIORS:
            errors.append(f"{case_id}: expected.behavior invalid")
        if set(_strings(expected.get("scope_source_ids"))) != set(source_ids):
            errors.append(f"{case_id}: expected scope must equal fixture sources")
        if str(expected.get("model_call_policy") or "") not in _MODEL_CALL_POLICIES:
            errors.append(f"{case_id}: model_call_policy invalid")
        if not _strings(expected.get("effective_actions")):
            errors.append(f"{case_id}: effective_actions required")
        for fact in _as_list(expected.get("required_fact_units")):
            if not isinstance(fact, Mapping) or not str(fact.get("id") or "").strip() or not _strings(fact.get("all_terms")):
                errors.append(f"{case_id}: required fact invalid")
                continue
            raw_groups = _as_list(fact.get("any_term_groups"))
            if raw_groups and len(_term_groups(raw_groups)) != len(raw_groups):
                errors.append(f"{case_id}: required fact alternative group invalid")
    if not cases:
        errors.append("cases required")
    if len(case_ids) != len(set(case_ids)):
        errors.append("case ids must be unique")
    return {
        "report_type": "structured_workflow_quality_suite_validation",
        "status": "passed" if not errors else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": suite.get("suite_id"),
        "version": suite.get("version"),
        "suite_hash": _suite_hash(suite),
        "case_count": len(cases),
        "workflows": workflows,
        "errors": errors,
    }


def _case_map(suite: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("id")): item
        for item in _as_list(suite.get("cases"))
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    }


def _raw_error_key_count(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(1 for key in value if str(key) == "error") + sum(
            _raw_error_key_count(item) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_raw_error_key_count(item) for item in value)
    return 0


def _field_statuses(trial: Mapping[str, Any]) -> dict[str, str]:
    quality = _mapping(trial.get("quality_report"))
    evidence = _mapping(quality.get("evidence"))
    fields = _mapping(evidence.get("field_evidence"))
    return {
        str(name): str(_mapping(item).get("status") or "")
        for name, item in fields.items()
    }


def _real_trace_ok(trace: Mapping[str, Any]) -> bool:
    calls = [item for item in _as_list(trace.get("calls")) if isinstance(item, Mapping)]
    return bool(calls) and all(
        call.get("mock") is not True
        and bool(str(call.get("model") or "").strip())
        and bool(str(call.get("generation_id") or "").strip())
        for call in calls
    )


def grade_trial(suite: Mapping[str, Any], trial: Mapping[str, Any]) -> dict[str, Any]:
    case_id = str(trial.get("case_id") or "")
    workflow = str(trial.get("workflow") or "")
    case = _case_map(suite).get(case_id)
    failures: list[str] = []
    if case is None:
        return {"status": "failed", "case_id": case_id, "workflow": workflow, "failures": ["unknown_case"]}
    expected = _mapping(case.get("expected"))
    text = str(trial.get("visible_text") or "")
    fact_results: list[dict[str, Any]] = []
    for fact in _as_list(expected.get("required_fact_units")):
        if not isinstance(fact, Mapping):
            continue
        terms = _strings(fact.get("all_terms"))
        alternative_groups = _term_groups(fact.get("any_term_groups"))
        present = bool(terms) and all(_contains(text, term) for term in terms) and all(
            any(_contains(text, term) for term in group)
            for group in alternative_groups
        )
        fact_results.append({"fact_id": fact.get("id"), "present": present})
        if not present:
            failures.append(f"required_fact_missing:{fact.get('id')}")
    fact_recall = (
        sum(1 for item in fact_results if item["present"]) / len(fact_results)
        if fact_results else 1.0
    )
    for group in _as_list(expected.get("required_answer_term_groups")):
        terms = _strings(group)
        if terms and not any(_contains(text, term) for term in terms):
            failures.append("required_answer_signal_missing")
    forbidden_hits = [term for term in _strings(expected.get("forbidden_terms")) if _contains(text, term)]
    if forbidden_hits:
        failures.append("forbidden_answer_content")

    expected_scope = set(_strings(expected.get("scope_source_ids")))
    scope = _mapping(trial.get("scope"))
    scope_ok = scope.get("mode") == "selected_sources" and set(_strings(scope.get("source_ids"))) == expected_scope
    if not scope_ok:
        failures.append("source_scope_mismatch")

    quality = _mapping(trial.get("quality_report"))
    guardrail = _mapping(trial.get("decision_guardrail"))
    readiness_consistent = (
        isinstance(quality.get("decision_ready"), bool)
        and quality.get("decision_ready") is guardrail.get("decision_ready")
    )
    if not readiness_consistent:
        failures.append("quality_guardrail_readiness_mismatch")
    action = str(guardrail.get("effective_action") or trial.get("effective_action") or "").strip()
    if action not in set(_strings(expected.get("effective_actions"))):
        failures.append("effective_action_mismatch")

    field_statuses = _field_statuses(trial)
    missing_material_fields = sorted(
        _MATERIAL_FIELDS.get(workflow, set()) - set(field_statuses)
    )
    unverified_fields = sorted(name for name, status in field_statuses.items() if status == "unverified")
    invalid_field_statuses = sorted(name for name, status in field_statuses.items() if status not in _SAFE_FIELD_STATUSES)
    if not field_statuses:
        failures.append("field_evidence_missing")
    if missing_material_fields:
        failures.append("material_field_evidence_missing")
    if unverified_fields:
        failures.append("unverified_material_fields")
    if invalid_field_statuses:
        failures.append("field_evidence_status_invalid")

    trace = _mapping(trial.get("llm_trace"))
    call_policy = str(expected.get("model_call_policy") or "")
    if call_policy == "required" and not _real_trace_ok(trace):
        failures.append("real_model_trace_missing")
    if call_policy == "forbidden" and int(trace.get("call_count") or 0) != 0:
        failures.append("model_called_for_abstention")
    raw_error_key_count = _raw_error_key_count(trace)
    if raw_error_key_count:
        failures.append("raw_error_field_exposed")

    route = _mapping(trial.get("route_identity"))
    expected_route_scope = str(_mapping(suite.get("route_scopes")).get(workflow) or "")
    route_ok = route.get("scope") == expected_route_scope and bool(str(route.get("fingerprint") or "").strip())
    if not route_ok:
        failures.append("route_identity_mismatch")
    if trial.get("persistence_ok") is not True:
        failures.append("persistence_mismatch")
    if str(expected.get("behavior") or "") == "abstain" and quality.get("decision_ready") is not False:
        failures.append("abstention_not_fail_closed")

    return {
        "status": "passed" if not failures else "failed",
        "case_id": case_id,
        "workflow": workflow,
        "trial_id": trial.get("trial_id"),
        "critical": case.get("critical") is True,
        "checks": {
            "required_facts": fact_recall == 1.0,
            "scope_exact": scope_ok,
            "readiness_consistent": readiness_consistent,
            "field_evidence": (
                bool(field_statuses)
                and not missing_material_fields
                and not unverified_fields
                and not invalid_field_statuses
            ),
            "model_call_policy": not any(item in failures for item in ("real_model_trace_missing", "model_called_for_abstention")),
            "route_identity": route_ok,
            "persistence": trial.get("persistence_ok") is True,
            "public_trace_safe": raw_error_key_count == 0,
        },
        "metrics": {
            "fact_recall": round(fact_recall, 6),
            "scope_accuracy": 1.0 if scope_ok else 0.0,
            "missing_material_field_count": len(missing_material_fields),
            "unverified_material_field_count": len(unverified_fields),
            "duration_ms": max(0, int(trial.get("duration_ms") or 0)),
            "cost_usd": max(0.0, float(trace.get("total_cost_usd") or 0.0)),
        },
        "fact_results": fact_results,
        "field_statuses": field_statuses,
        "missing_material_fields": missing_material_fields,
        "unverified_fields": unverified_fields,
        "forbidden_hits": forbidden_hits,
        "failures": list(dict.fromkeys(failures)),
    }


def evaluate_suite(suite: Mapping[str, Any], trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    validation = validate_suite(suite)
    grades = [grade_trial(suite, trial) for trial in trials]
    thresholds = _mapping(suite.get("thresholds"))
    expected_groups = {
        (workflow, str(case.get("id")))
        for workflow in _strings(suite.get("workflows"))
        for case in _as_list(suite.get("cases"))
        if isinstance(case, Mapping)
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for grade in grades:
        grouped[(str(grade.get("workflow")), str(grade.get("case_id")))].append(grade)
    group_results: list[dict[str, Any]] = []
    for workflow, case_id in sorted(expected_groups):
        items = grouped.get((workflow, case_id), [])
        pass_rate = sum(1 for item in items if item.get("status") == "passed") / len(items) if items else 0.0
        enough = len(items) >= int(thresholds.get("min_trials_per_case") or 1)
        group_results.append({
            "workflow": workflow,
            "case_id": case_id,
            "trial_count": len(items),
            "pass_rate": round(pass_rate, 6),
            "status": "passed" if enough and pass_rate >= float(thresholds.get("min_trial_pass_rate") or 1.0) else "failed",
        })
    fact_recall = sum(float(item.get("metrics", {}).get("fact_recall", 0.0)) for item in grades) / len(grades) if grades else 0.0
    scope_accuracy = sum(float(item.get("metrics", {}).get("scope_accuracy", 0.0)) for item in grades) / len(grades) if grades else 0.0
    unverified = sum(int(item.get("metrics", {}).get("unverified_material_field_count", 0)) for item in grades)
    passed = bool(
        validation["status"] == "passed"
        and grades
        and all(item["status"] == "passed" for item in group_results)
        and fact_recall >= float(thresholds.get("min_fact_recall") or 1.0)
        and scope_accuracy >= float(thresholds.get("min_scope_accuracy") or 1.0)
        and unverified <= int(thresholds.get("max_unverified_material_fields") or 0)
    )
    return {
        "report_type": "structured_workflow_quality_evaluation",
        "status": "passed" if passed else "failed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_validation": validation,
        "trial_count": len(grades),
        "passed_trial_count": sum(1 for item in grades if item.get("status") == "passed"),
        "metrics": {
            "fact_recall": round(fact_recall, 6),
            "scope_accuracy": round(scope_accuracy, 6),
            "unverified_material_field_count": unverified,
        },
        "groups": group_results,
        "grades": grades,
    }


__all__ = [
    "default_suite_path",
    "evaluate_suite",
    "grade_trial",
    "load_suite",
    "validate_suite",
]
