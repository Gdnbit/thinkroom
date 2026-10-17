"""Append-only repeated live audit for briefing and committee workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ulid import ULID

from app.config import settings
from app.verification.decision_quality_live_runner import _seed_case
from app.verification.structured_workflow_quality import (
    default_suite_path,
    evaluate_suite,
    load_suite,
    validate_suite,
)


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _select_cases(suite: Mapping[str, Any], case_ids: Sequence[str] | None) -> list[Mapping[str, Any]]:
    cases = [item for item in _as_list(suite.get("cases")) if isinstance(item, Mapping)]
    if not case_ids:
        return cases
    requested = list(dict.fromkeys(str(item).strip() for item in case_ids if str(item or "").strip()))
    by_id = {str(item.get("id")): item for item in cases}
    unknown = [item for item in requested if item not in by_id]
    if unknown:
        raise ValueError(f"unknown case ids: {', '.join(unknown)}")
    return [by_id[item] for item in requested]


def build_run_plan(
    suite: Mapping[str, Any],
    *,
    case_ids: Sequence[str] | None,
    repeats: int,
    candidate_id: str,
) -> dict[str, Any]:
    cases = _select_cases(suite, case_ids)
    workflows = [str(item) for item in _as_list(suite.get("workflows"))]
    repeat_count = max(1, int(repeats))
    return {
        "report_type": "structured_workflow_quality_live_plan",
        "status": "planned",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": suite.get("suite_id"),
        "suite_version": suite.get("version"),
        "candidate_id": candidate_id,
        "case_ids": [item.get("id") for item in cases],
        "workflows": workflows,
        "repeats": repeat_count,
        "trial_count": len(cases) * len(workflows) * repeat_count,
        "workflow_request_count": len(cases) * len(workflows) * repeat_count,
        "external_model_call_count": "determined_by_runtime_routes_and_abstention",
        "database_policy": "append_only_no_cleanup",
    }


class LiveRequestError(RuntimeError):
    def __init__(self, *, error_type: str, status_code: int | None = None):
        self.error_type = error_type
        self.status_code = status_code
        super().__init__(error_type)


def _request_json(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    api_key = str(getattr(settings, "APP_API_KEY", "") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as exc:
        exc.read()
        raise LiveRequestError(error_type="http_error", status_code=exc.code) from None
    except TimeoutError:
        raise LiveRequestError(error_type="timeout") from None
    except (urllib.error.URLError, json.JSONDecodeError):
        raise LiveRequestError(error_type="transport_or_json_error") from None


def _remap_source_ids(value: Any, runtime_to_logical: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"source_ids", "requested_source_ids", "matched_source_ids"} and isinstance(item, list):
                result[str(key)] = [runtime_to_logical.get(str(source_id), str(source_id)) for source_id in item]
            else:
                result[str(key)] = _remap_source_ids(item, runtime_to_logical)
        return result
    if isinstance(value, list):
        return [_remap_source_ids(item, runtime_to_logical) for item in value]
    return value


def _visible_text(workflow: str, response: Mapping[str, Any], detail: Mapping[str, Any]) -> str:
    if workflow == "briefing":
        values = [
            response.get("title"),
            response.get("executive_summary"),
            *_as_list(response.get("supporting_points")),
            *_as_list(response.get("contrary_points")),
            *_as_list(response.get("key_risks")),
            response.get("strategy_fit_summary"),
        ]
    else:
        adjudication = _mapping(detail.get("adjudication"))
        values = [
            response.get("synthesis"),
            *_as_list(response.get("risk_warnings")),
            *_as_list(response.get("structure_warnings")),
            *_as_list(response.get("evidence_quality_warnings")),
            response.get("hedging_advice"),
            adjudication.get("synthesis"),
            *_as_list(adjudication.get("key_agreements")),
            *_as_list(adjudication.get("key_disagreements")),
            adjudication.get("minority_view"),
        ]
    return "\n".join(str(item).strip() for item in values if str(item or "").strip())


def _persistence_ok(workflow: str, response: Mapping[str, Any], detail: Mapping[str, Any]) -> bool:
    response_route = _mapping(response.get("route_identity"))
    detail_route = _mapping(detail.get("route_identity"))
    response_trace = _mapping(response.get("llm_trace"))
    detail_trace = _mapping(detail.get("llm_trace"))
    common = bool(
        response_route.get("fingerprint")
        and response_route.get("fingerprint") == detail_route.get("fingerprint")
        and response_trace.get("call_count") == detail_trace.get("call_count")
        and _mapping(response.get("decision_guardrail")).get("status")
        == _mapping(detail.get("decision_guardrail")).get("status")
        and _mapping(response.get("quality_report")).get("decision_ready")
        is _mapping(detail.get("quality_report")).get("decision_ready")
    )
    if workflow == "briefing":
        return common and bool(
            response.get("id") == detail.get("id")
            and response.get("direction") == detail.get("direction")
            and response.get("recommended_action") == detail.get("recommended_action")
            and response.get("executive_summary") == detail.get("executive_summary")
        )
    adjudication = _mapping(detail.get("adjudication"))
    return common and bool(
        response.get("debate_id") == detail.get("id")
        and response.get("direction") == adjudication.get("direction")
        and response.get("recommended_action") == adjudication.get("recommended_action")
        and response.get("synthesis") == adjudication.get("synthesis")
    )


def build_trial_from_responses(
    *,
    workflow: str,
    case_id: str,
    candidate_id: str,
    trial_id: str,
    response: Mapping[str, Any],
    detail: Mapping[str, Any],
    runtime_to_logical: Mapping[str, str],
) -> dict[str, Any]:
    remapped_response = _remap_source_ids(response, runtime_to_logical)
    remapped_detail = _remap_source_ids(detail, runtime_to_logical)
    return {
        "workflow": workflow,
        "case_id": case_id,
        "candidate_id": candidate_id,
        "trial_id": trial_id,
        "object_id": response.get("id") if workflow == "briefing" else response.get("debate_id"),
        "visible_text": _visible_text(workflow, remapped_response, remapped_detail),
        "scope": remapped_response.get("scope") if isinstance(remapped_response.get("scope"), Mapping) else {},
        "quality_report": remapped_response.get("quality_report") if isinstance(remapped_response.get("quality_report"), Mapping) else {},
        "decision_guardrail": remapped_response.get("decision_guardrail") if isinstance(remapped_response.get("decision_guardrail"), Mapping) else {},
        "llm_trace": remapped_response.get("llm_trace") if isinstance(remapped_response.get("llm_trace"), Mapping) else {},
        "route_identity": remapped_response.get("route_identity") if isinstance(remapped_response.get("route_identity"), Mapping) else {},
        "effective_action": _mapping(remapped_response.get("decision_guardrail")).get("effective_action") or remapped_response.get("recommended_action"),
        "duration_ms": remapped_response.get("duration_ms"),
        "persistence_ok": _persistence_ok(workflow, remapped_response, remapped_detail),
    }


def _route_identities(trials: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    identities: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for workflow in sorted({str(item.get("workflow")) for item in trials}):
        candidates = [
            dict(_mapping(item.get("route_identity")))
            for item in trials
            if item.get("workflow") == workflow and _mapping(item.get("route_identity")).get("fingerprint")
        ]
        fingerprints = sorted({str(item.get("fingerprint")) for item in candidates})
        if len(candidates) != sum(1 for item in trials if item.get("workflow") == workflow):
            errors.append({"workflow": workflow, "error_type": "route_identity_missing"})
        elif len(fingerprints) != 1:
            errors.append({"workflow": workflow, "error_type": "route_identity_changed"})
        else:
            identities[workflow] = candidates[0]
    return identities, errors


def _redact_trial(trial: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize controlled-fixture output so the report can be replayed."""
    text = str(trial.get("visible_text") or "")
    return dict(trial) | {
        "visible_text": text,
        "visible_content_length": len(text),
        "visible_content_hash": f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
    }


def run_live_audit(
    suite: Mapping[str, Any],
    *,
    base_url: str,
    case_ids: Sequence[str] | None,
    repeats: int,
    candidate_id: str,
    timeout: float,
) -> dict[str, Any]:
    validation = validate_suite(suite)
    if validation["status"] != "passed":
        return {"report_type": "structured_workflow_quality_live_audit", "status": "failed", "suite_validation": validation, "errors": [{"error_type": "suite_validation_failed"}]}
    plan = build_run_plan(suite, case_ids=case_ids, repeats=repeats, candidate_id=candidate_id)
    run_id = str(ULID())
    trials: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seed_records: list[dict[str, Any]] = []
    _request_json(base_url=base_url, method="GET", path="/health", timeout=min(timeout, 10))
    for case in _select_cases(suite, case_ids):
        case_id = str(case.get("id"))
        seed = _seed_case(case, run_id=run_id)
        runtime_source_ids = list(seed["logical_to_runtime"].values())
        seed_records.append({"case_id": case_id, "source_ids": runtime_source_ids, "claim_ids": seed["claim_ids"]})
        for workflow in _as_list(suite.get("workflows")):
            workflow = str(workflow)
            for repeat_index in range(1, max(1, int(repeats)) + 1):
                trial_id = f"{run_id}:{workflow}:{case_id}:{repeat_index}"
                try:
                    if workflow == "briefing":
                        response = _request_json(
                            base_url=base_url,
                            method="POST",
                            path="/api/v1/briefings",
                            payload={"question": case.get("question"), "asset_scope": ["GENERAL"], "source_ids": runtime_source_ids, "committee_level": "single"},
                            timeout=timeout,
                        )
                        detail_path = f"/api/v1/briefings/{response.get('id')}"
                    else:
                        response = _request_json(
                            base_url=base_url,
                            method="POST",
                            path="/api/v1/committee/run",
                            payload={"question": case.get("question"), "asset": "GENERAL", "num_rounds": 1, "source_ids": runtime_source_ids},
                            timeout=timeout,
                        )
                        detail_path = f"/api/v1/committee/{response.get('debate_id')}"
                    detail = _request_json(base_url=base_url, method="GET", path=detail_path, timeout=min(timeout, 30))
                    trial = build_trial_from_responses(
                        workflow=workflow,
                        case_id=case_id,
                        candidate_id=candidate_id,
                        trial_id=trial_id,
                        response=response,
                        detail=detail,
                        runtime_to_logical=seed["runtime_to_logical"],
                    )
                except Exception as exc:
                    error = {
                        "workflow": workflow,
                        "case_id": case_id,
                        "trial_id": trial_id,
                        "error_type": getattr(exc, "error_type", type(exc).__name__),
                        "status_code": getattr(exc, "status_code", None),
                    }
                    errors.append(error)
                    trial = {
                        "workflow": workflow,
                        "case_id": case_id,
                        "candidate_id": candidate_id,
                        "trial_id": trial_id,
                        "visible_text": "",
                        "scope": {"mode": "selected_sources", "source_ids": list(seed["logical_to_runtime"].keys())},
                        "quality_report": {},
                        "decision_guardrail": {},
                        "llm_trace": {},
                        "route_identity": {},
                        "persistence_ok": False,
                    }
                trials.append(trial)
    evaluation = evaluate_suite(suite, trials)
    route_identities, route_errors = _route_identities(trials)
    errors.extend(route_errors)
    status = "passed" if evaluation["status"] == "passed" and not errors else "failed"
    return {
        "report_type": "structured_workflow_quality_live_audit",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "candidate_id": candidate_id,
        "base_url": base_url,
        "plan": plan,
        "suite_validation": validation,
        "database_policy": "append_only_no_cleanup",
        "seed_records": seed_records,
        "route_identities": route_identities,
        "trial_count": len(trials),
        "errors": errors,
        "trials": [_redact_trial(item) for item in trials],
        "evaluation": {**evaluation, "grades": evaluation.get("grades", [])},
    }


def _stamp(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_report_bundle(report: dict[str, Any], latest_path: str | Path) -> tuple[Path, Path]:
    latest = Path(latest_path)
    latest.parent.mkdir(parents=True, exist_ok=True)
    if latest.exists():
        try:
            previous = json.loads(latest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = None
        if isinstance(previous, dict):
            archive = latest.with_name(f"structured-workflow-quality-live-audit-{_stamp(previous.get('generated_at'))}.json")
            if not archive.exists():
                archive.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = latest.with_name(f"structured-workflow-quality-live-audit-{_stamp(report.get('generated_at'))}.json")
    report["report_path"] = str(archive)
    report["latest_path"] = str(latest)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    archive.write_text(serialized, encoding="utf-8")
    latest.write_text(serialized, encoding="utf-8")
    return archive, latest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or run repeated structured-workflow quality trials.")
    parser.add_argument("--suite", default=str(default_suite_path()))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--candidate-id", default="current-structured-routes")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-external-model-calls", action="store_true")
    parser.add_argument("--report")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args(argv)
    suite = load_suite(args.suite)
    if args.execute and not args.allow_external_model_calls:
        report: dict[str, Any] = {"report_type": "structured_workflow_quality_live_audit", "status": "blocked", "generated_at": datetime.now(timezone.utc).isoformat(), "errors": [{"error_type": "external_model_calls_not_allowed"}]}
    elif args.execute:
        report = run_live_audit(suite, base_url=args.base_url, case_ids=args.case_ids, repeats=args.repeats, candidate_id=args.candidate_id, timeout=args.timeout)
    else:
        report = build_run_plan(suite, case_ids=args.case_ids, repeats=args.repeats, candidate_id=args.candidate_id)
    root = Path(__file__).resolve().parents[4]
    report_path = Path(args.report) if args.report else root / "docs" / "verification" / "structured-workflow-quality-live-audit-latest.json"
    write_report_bundle(report, report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_error and report.get("status") not in {"passed", "planned"} else 0


__all__ = [
    "build_run_plan",
    "build_trial_from_responses",
    "run_live_audit",
    "write_report_bundle",
]


if __name__ == "__main__":
    sys.exit(main())
