"""Append-only live runner for the controlled decision-quality suite."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ulid import ULID

from app.config import settings
from app.database import safe_commit
from app.dependencies import SessionLocal
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.raw_source import RawSource
from app.verification.decision_quality_eval import (
    default_suite_path,
    evaluate_suite,
    grade_trial,
    load_suite,
    validate_suite,
)


def _new_id() -> str:
    return str(ULID())


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def select_cases(suite: Mapping[str, Any], case_ids: Sequence[str] | None = None) -> list[Mapping[str, Any]]:
    cases = [item for item in _as_list(suite.get("cases")) if isinstance(item, Mapping)]
    if not case_ids:
        return cases
    requested = list(dict.fromkeys(str(item).strip() for item in case_ids if str(item or "").strip()))
    by_id = {str(case.get("id")): case for case in cases}
    unknown = [case_id for case_id in requested if case_id not in by_id]
    if unknown:
        raise ValueError(f"unknown case ids: {', '.join(unknown)}")
    return [by_id[case_id] for case_id in requested]


def build_run_plan(
    suite: Mapping[str, Any],
    *,
    case_ids: Sequence[str] | None,
    repeats: int,
    candidate_id: str,
) -> dict[str, Any]:
    selected = select_cases(suite, case_ids)
    repeat_count = max(1, int(repeats))
    return {
        "report_type": "decision_quality_live_plan",
        "status": "planned",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_id": suite.get("suite_id"),
        "suite_version": suite.get("version"),
        "candidate_id": candidate_id,
        "case_ids": [case.get("id") for case in selected],
        "case_count": len(selected),
        "repeats": repeat_count,
        "trial_count": len(selected) * repeat_count,
        "external_model_call_requests": len(selected) * repeat_count,
        "database_policy": "append_only_no_cleanup",
        "records_preserved": ["raw_sources", "claim_cards", "chat_sessions", "chat_messages", "decision_traces", "reports"],
    }


def _remap_source_ids(values: Any, runtime_to_logical: Mapping[str, str]) -> list[str]:
    return [
        runtime_to_logical.get(str(value), str(value))
        for value in _as_list(values)
        if str(value or "").strip()
    ]


def build_trial_from_response(
    *,
    case_id: str,
    candidate_id: str,
    trial_id: str,
    response: Mapping[str, Any],
    runtime_to_logical: Mapping[str, str],
) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    for position, raw in enumerate(_as_list(response.get("citations")), start=1):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        item["index"] = item.get("index") or position
        source_id = str(item.get("source_id") or "").strip()
        if source_id:
            item["source_id"] = runtime_to_logical.get(source_id, source_id)
        citations.append(item)

    raw_scope = _as_mapping(response.get("scope"))
    scope = dict(raw_scope)
    scope["source_ids"] = _remap_source_ids(raw_scope.get("source_ids"), runtime_to_logical)
    return {
        "case_id": case_id,
        "candidate_id": candidate_id,
        "trial_id": trial_id,
        "message_id": response.get("id"),
        "answer": str(response.get("content") or ""),
        "citations": citations,
        "scope": scope,
        "quality_report": response.get("quality_report") if isinstance(response.get("quality_report"), Mapping) else {},
        "quality_filters": response.get("quality_filters") if isinstance(response.get("quality_filters"), Mapping) else {},
        "decision_guardrail": response.get("decision_guardrail") if isinstance(response.get("decision_guardrail"), Mapping) else {},
        "llm_trace": response.get("llm_trace") if isinstance(response.get("llm_trace"), Mapping) else {},
        "route_identity": response.get("route_identity") if isinstance(response.get("route_identity"), Mapping) else {},
        "timing_trace": response.get("timing_trace") if isinstance(response.get("timing_trace"), Mapping) else {},
        "model_used": response.get("model_used"),
    }


def _live_audit_status(
    *,
    errors: Sequence[Mapping[str, Any]],
    grades: Sequence[Mapping[str, Any]],
    evaluation: Mapping[str, Any],
    candidate_id: str,
) -> str:
    if errors or not grades or any(grade.get("status") != "passed" for grade in grades):
        return "failed"
    candidates = evaluation.get("candidates")
    candidate = candidates.get(candidate_id) if isinstance(candidates, Mapping) else None
    return "passed" if isinstance(candidate, Mapping) and candidate.get("status") == "passed" else "failed"


def _route_identity_from_trials(
    trials: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    identities: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for trial in trials:
        case_id = str(trial.get("case_id") or "")
        trial_id = str(trial.get("trial_id") or "")
        identity = _as_mapping(trial.get("route_identity"))
        fingerprint = str(identity.get("fingerprint") or "").strip()
        if not fingerprint:
            errors.append(
                {
                    "case_id": case_id,
                    "trial_id": trial_id,
                    "error_type": "route_identity_missing",
                    "error": "chat response did not expose a decision route fingerprint",
                }
            )
            continue
        identities.append(dict(identity))

    fingerprints = sorted({str(identity.get("fingerprint")) for identity in identities})
    if len(fingerprints) > 1:
        errors.append(
            {
                "error_type": "route_identity_changed_during_audit",
                "error": "decision route fingerprint changed during the live audit",
                "fingerprints": fingerprints,
            }
        )
    return (identities[0] if len(fingerprints) == 1 else {}), errors


def _parse_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _published_at(content: str) -> datetime | None:
    import re

    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", content or "")
    return _parse_date(match.group(1)) if match else None


def _source_type(value: Any) -> str:
    raw = str(value or "note")
    return {
        "controlled_report": "report",
        "controlled_note": "note",
        "chat_record": "chat",
    }.get(raw, raw if raw in {"article", "video", "tweet", "report", "note", "chat", "image"} else "note")


def _trust_weight(value: Any) -> float:
    return {"high": 0.9, "medium": 0.6, "low": 0.2}.get(str(value or "").lower(), 0.5)


def _stance(content: str) -> str:
    normalized = (content or "").lower()
    bullish = ("增长" in normalized or "走强" in normalized) and "下降" not in normalized
    bearish = "下降" in normalized and "增长" not in normalized
    if bullish:
        return "bullish"
    if bearish:
        return "bearish"
    return "neutral"


def _geographic_scope(source: Mapping[str, Any], content: str) -> list[str]:
    explicit = [str(item) for item in _as_list(source.get("geographic_scope")) if str(item or "").strip()]
    if explicit:
        return explicit
    scopes: list[str] = []
    if "中国" in content:
        scopes.append("CN")
    if "欧洲" in content:
        scopes.append("EU")
    return scopes


def _seed_case(case: Mapping[str, Any], *, run_id: str) -> dict[str, Any]:
    case_id = str(case.get("id") or "")
    fixture = _as_mapping(case.get("fixture"))
    behavior = str(_as_mapping(case.get("expected")).get("behavior") or "")
    category = str(case.get("category") or "")
    logical_to_runtime: dict[str, str] = {}
    claim_ids: list[str] = []
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        for source_number, source in enumerate(_as_list(fixture.get("sources")), start=1):
            if not isinstance(source, Mapping):
                continue
            logical_id = str(source.get("source_id") or "").strip()
            runtime_source_id = _new_id()
            runtime_claim_id = _new_id()
            content = str(source.get("content") or "").strip()
            marker = f"DQ_EVAL_{run_id}_{source_number}"
            published_at = _published_at(content)
            valid_until = _parse_date(source.get("valid_until"))
            time_sensitive = category in {"contradiction", "temporal_staleness"}
            if category == "contradiction" and valid_until is None:
                valid_until = now + timedelta(days=30)
            trust_tier = str(source.get("trust_tier") or "medium")

            raw_source = RawSource(
                id=runtime_source_id,
                source_type=_source_type(source.get("source_type")),
                title=f"Decision quality eval {case_id} {logical_id} {run_id}",
                publisher="decision-quality-eval",
                published_at=published_at,
                raw_text=content,
                article_summary=content,
                language="zh",
                status="completed",
                ingested_at=now,
                word_count=len(content),
                content_hash=hashlib.sha256(f"{run_id}:{logical_id}:{content}".encode("utf-8")).hexdigest(),
                user_trust_level=trust_tier,
                collection_reason="reference",
                user_note="append-only controlled decision-quality evaluation fixture",
                created_at=now,
                updated_at=now,
            )
            claim = ClaimCard(
                id=runtime_claim_id,
                summary=content,
                full_quote=content,
                contextual_text=f"受控评测；来源 {logical_id}；类别 {category}；{content}",
                claim_type="data_observation" if behavior != "abstain" else "risk_warning",
                stance=_stance(content),
                time_horizon="short" if time_sensitive else "unspecified",
                asset_scope=[],
                theme_scope=["decision_quality_eval", category],
                geographic_scope=_geographic_scope(source, content),
                conditions=[],
                invalidation_conditions=[],
                reasoning_basis=[content],
                confidence=0.95 if trust_tier == "high" else 0.5,
                source_trust_weight=_trust_weight(trust_tier),
                is_contradicted=False,
                status="active",
                valid_from=published_at,
                valid_until=valid_until,
                time_sensitive=time_sensitive,
                expiry_hint=str(source.get("valid_until") or "") or None,
                version=1,
                created_at=now,
                updated_at=now,
            )
            claim.raw_source_links.append(
                ClaimRawSourceLink(
                    claim_id=runtime_claim_id,
                    source_id=runtime_source_id,
                    link_type="decision_quality_eval",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(raw_source)
            db.add(claim)
            logical_to_runtime[logical_id] = runtime_source_id
            claim_ids.append(runtime_claim_id)
        safe_commit(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {
        "case_id": case_id,
        "logical_to_runtime": logical_to_runtime,
        "runtime_to_logical": {runtime: logical for logical, runtime in logical_to_runtime.items()},
        "claim_ids": claim_ids,
    }


def _request_json(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            details: Any = json.loads(raw)
        except json.JSONDecodeError:
            details = raw[:1000]
        raise RuntimeError(f"HTTP {exc.code} {path}: {details}") from exc


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
        return {
            "report_type": "decision_quality_live_audit",
            "status": "failed",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "suite_validation": validation,
            "errors": ["suite_validation_failed"],
        }

    selected = select_cases(suite, case_ids)
    plan = build_run_plan(suite, case_ids=case_ids, repeats=repeats, candidate_id=candidate_id)
    run_id = _new_id()
    trials: list[dict[str, Any]] = []
    seed_records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    _request_json(base_url=base_url, method="GET", path="/health", timeout=min(timeout, 10))
    for case in selected:
        case_id = str(case.get("id"))
        seed = _seed_case(case, run_id=run_id)
        seed_records.append({
            "case_id": case_id,
            "source_ids": list(seed["logical_to_runtime"].values()),
            "claim_ids": seed["claim_ids"],
        })
        runtime_source_ids = list(seed["logical_to_runtime"].values())
        for repeat_index in range(1, max(1, int(repeats)) + 1):
            trial_id = f"{run_id}:{case_id}:{repeat_index}"
            try:
                session = _request_json(
                    base_url=base_url,
                    method="POST",
                    path="/api/v1/chat/sessions",
                    payload={"title": f"Decision Quality {case_id} {run_id}", "mode": "chat", "asset": "GENERAL"},
                    timeout=min(timeout, 30),
                )
                session_id = str(session.get("id") or "")
                response = _request_json(
                    base_url=base_url,
                    method="POST",
                    path=f"/api/v1/chat/sessions/{session_id}/chat",
                    payload={
                        "message": str(case.get("question") or ""),
                        "history": [],
                        "scope_mode": "selected_sources",
                        "source_ids": runtime_source_ids,
                    },
                    timeout=timeout,
                )
                trial = build_trial_from_response(
                    case_id=case_id,
                    candidate_id=candidate_id,
                    trial_id=trial_id,
                    response=response,
                    runtime_to_logical=seed["runtime_to_logical"],
                )
                trial["session_id"] = session_id
            except Exception as exc:
                error = {"case_id": case_id, "trial_id": trial_id, "error_type": type(exc).__name__, "error": str(exc)[:1000]}
                errors.append(error)
                trial = {
                    "case_id": case_id,
                    "candidate_id": candidate_id,
                    "trial_id": trial_id,
                    "answer": "",
                    "citations": [],
                    "scope": {"mode": "selected_sources", "source_ids": list(seed["logical_to_runtime"].keys())},
                    "quality_report": {},
                    "decision_guardrail": {},
                    "llm_trace": {},
                    "timing_trace": {},
                    "runner_error": error,
                }
            trials.append(trial)

    evaluation = evaluate_suite(suite, trials)
    grades = [grade_trial(suite, trial) for trial in trials]
    route_identity, route_identity_errors = _route_identity_from_trials(trials)
    errors.extend(route_identity_errors)
    audit_status = _live_audit_status(
        errors=errors,
        grades=grades,
        evaluation=evaluation,
        candidate_id=candidate_id,
    )
    return {
        "report_type": "decision_quality_live_audit",
        "status": audit_status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "base_url": base_url,
        "candidate_id": candidate_id,
        "route_identity": route_identity,
        "plan": plan,
        "suite_validation": validation,
        "database_policy": "append_only_no_cleanup",
        "seed_records": seed_records,
        "trial_count": len(trials),
        "passed_trial_count": sum(1 for grade in grades if grade.get("status") == "passed"),
        "errors": errors,
        "trials": trials,
        "grades": grades,
        "promotion_evaluation": evaluation,
    }


def _report_stamp(value: Any) -> str:
    parsed = _parse_date(value) or datetime.now(timezone.utc)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def write_report_bundle(report: dict[str, Any], latest_path: str | Path) -> tuple[Path, Path]:
    """Write an immutable timestamped report plus the latest pointer.

    A pre-existing latest report is archived first when it predates the current
    report. No report is deleted or replaced without an immutable copy.
    """
    latest = Path(latest_path)
    latest.parent.mkdir(parents=True, exist_ok=True)
    if latest.exists():
        try:
            previous = json.loads(latest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = None
        if isinstance(previous, dict):
            previous_archive = latest.with_name(
                f"decision-quality-live-audit-{_report_stamp(previous.get('generated_at'))}.json"
            )
            if not previous_archive.exists():
                previous_archive.write_text(
                    json.dumps(previous, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    archive = latest.with_name(
        f"decision-quality-live-audit-{_report_stamp(report.get('generated_at'))}.json"
    )
    report["report_path"] = str(archive)
    report["latest_path"] = str(latest)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    archive.write_text(serialized, encoding="utf-8")
    latest.write_text(serialized, encoding="utf-8")
    return archive, latest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or run append-only real-model decision-quality trials.")
    parser.add_argument("--suite", default=str(default_suite_path()))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--candidate-id", default="current-route")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-external-model-calls", action="store_true")
    parser.add_argument("--report")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args(argv)

    suite = load_suite(args.suite)
    if args.execute and not args.allow_external_model_calls:
        report: dict[str, Any] = {
            "report_type": "decision_quality_live_audit",
            "status": "blocked",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "errors": ["--execute requires --allow-external-model-calls"],
        }
    elif args.execute:
        report = run_live_audit(
            suite,
            base_url=args.base_url,
            case_ids=args.case_ids,
            repeats=args.repeats,
            candidate_id=args.candidate_id,
            timeout=args.timeout,
        )
    else:
        report = build_run_plan(
            suite,
            case_ids=args.case_ids,
            repeats=args.repeats,
            candidate_id=args.candidate_id,
        )

    root = Path(__file__).resolve().parents[4]
    report_path = Path(args.report) if args.report else root / "docs" / "verification" / "decision-quality-live-audit-latest.json"
    write_report_bundle(report, report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = report.get("status") not in {"passed", "planned"}
    return 1 if args.fail_on_error and failed else 0


__all__ = [
    "build_run_plan",
    "build_trial_from_response",
    "_route_identity_from_trials",
    "run_live_audit",
    "select_cases",
    "write_report_bundle",
]


if __name__ == "__main__":
    sys.exit(main())
