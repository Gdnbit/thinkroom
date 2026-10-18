"""Runtime smoke gate for the primary Decision Chat workflow.

This module intentionally talks to a running backend over HTTP. It is not part
of the deterministic unit test suite by default; use it when validating a live
local or staging deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REQUIRED_CHAT_TRACE_EVENTS = {"retrieval", "scope", "quality", "guardrail", "llm_call"}
REQUIRED_STRUCTURED_TRACE_EVENTS = {"retrieval", "scope", "quality", "guardrail"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _repo_root() / "docs" / "verification" / f"main_flow_runtime_smoke_{stamp}.json"


def latest_output_path(output_path: str | Path | None = None) -> Path:
    base_dir = Path(output_path).parent if output_path else _repo_root() / "docs" / "verification"
    return base_dir / "main_flow_runtime_smoke_latest.json"


def _json_request(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local/staging smoke target.
            raw = response.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw)
            except json.JSONDecodeError:
                body = raw
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "body": body,
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return {
            "ok": False,
            "status": exc.code,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "body": body,
        }
    except (TimeoutError, URLError, OSError) as exc:
        return {
            "ok": False,
            "status": None,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }


def _query(path: str, params: dict[str, Any]) -> str:
    return f"{path}?{urlencode({key: value for key, value in params.items() if value is not None})}"


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _body(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body")
    return body if isinstance(body, dict) else {}


def _reference_time_present(content: str, payload: dict[str, Any]) -> bool:
    reference_time = payload.get("reference_time")
    if isinstance(reference_time, str) and reference_time:
        return reference_time in content
    return "截至" in content or "参考时间" in content or "生成时间" in content


def _visible_caution_present(content: str) -> bool:
    caution_terms = (
        "资料不足",
        "证据不足",
        "无引用",
        "没有引用",
        "未找到引用",
        "需要复核",
        "不能直接作为",
        "仅作为研究线索",
        "决策护栏",
    )
    return any(term in content for term in caution_terms)


def _scope_summary(payload: dict[str, Any]) -> dict[str, Any]:
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    return {
        "mode": scope.get("mode"),
        "source_ids": scope.get("source_ids") if isinstance(scope.get("source_ids"), list) else [],
    }


def _route_identity_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    identity = payload.get("route_identity") if isinstance(payload.get("route_identity"), dict) else None
    if identity is None:
        return None
    allowed_fields = (
        "schema_version",
        "scope",
        "contract_version",
        "fingerprint",
        "configuration_fingerprint",
        "implementation_fingerprint",
        "implementation_file_count",
    )
    return {key: identity.get(key) for key in allowed_fields if identity.get(key) is not None}


def _llm_trace_summary(payload: dict[str, Any]) -> dict[str, Any]:
    trace = payload.get("llm_trace") if isinstance(payload.get("llm_trace"), dict) else {}
    calls = trace.get("calls") if isinstance(trace.get("calls"), list) else []
    allowed_fields = (
        "step",
        "model",
        "requested_model",
        "configured_fallback_models",
        "fallback_chain",
        "skill_role",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "cost_source",
        "generation_id",
        "mock",
        "error_type",
    )
    sanitized_calls = [
        {key: call.get(key) for key in allowed_fields if call.get(key) is not None}
        for call in calls
        if isinstance(call, dict)
    ]
    return {
        "call_count": trace.get("call_count") if isinstance(trace.get("call_count"), int) else len(sanitized_calls),
        "total_cost_usd": trace.get("total_cost_usd"),
        "total_input_tokens": trace.get("total_input_tokens"),
        "total_output_tokens": trace.get("total_output_tokens"),
        "integrity_retry_attempted": trace.get("integrity_retry_attempted"),
        "integrity_retry_succeeded": trace.get("integrity_retry_succeeded"),
        "integrity_prune_succeeded": trace.get("integrity_prune_succeeded"),
        "integrity_fallback_applied": trace.get("integrity_fallback_applied"),
        "integrity_retry_reasons": trace.get("integrity_retry_reasons"),
        "integrity_fallback_reasons": trace.get("integrity_fallback_reasons"),
        "calls": sanitized_calls,
    }


def _chat_step(result: dict[str, Any]) -> dict[str, Any]:
    body = _body(result)
    content = body.get("content") if isinstance(body.get("content"), str) else ""
    citations = body.get("citations") if isinstance(body.get("citations"), list) else []
    guardrail = body.get("decision_guardrail") if isinstance(body.get("decision_guardrail"), dict) else {}
    quality = body.get("quality_report") if isinstance(body.get("quality_report"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms"),
        "message_id": body.get("id"),
        "content_length": len(content),
        "content_hash": f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
        "citations_count": len(citations),
        "citation_source_ids": sorted({
            citation.get("source_id")
            for citation in citations
            if isinstance(citation, dict) and isinstance(citation.get("source_id"), str)
        }),
        "scope": _scope_summary(body),
        "route_identity": _route_identity_summary(body),
        "llm_trace": _llm_trace_summary(body),
        "timing_trace": body.get("timing_trace") if _is_record(body.get("timing_trace")) else None,
        "quality_report_present": _is_record(body.get("quality_report")),
        "decision_guardrail_present": _is_record(body.get("decision_guardrail")),
        "guardrail_status": guardrail.get("status"),
        "guardrail_decision_ready": guardrail.get("decision_ready"),
        "reference_time_present": _reference_time_present(content, body),
        "visible_caution_present": _visible_caution_present(content),
        "model_used": body.get("model_used"),
        "quality_severity": quality.get("severity"),
        "quality_decision_ready": quality.get("decision_ready"),
        "warning_codes": [
            warning.get("code")
            for warning in (body.get("quality_report") or {}).get("warnings", [])
            if isinstance(warning, dict)
        ] if _is_record(body.get("quality_report")) else [],
    }


def _briefing_step(result: dict[str, Any]) -> dict[str, Any]:
    body = _body(result)
    summary = body.get("executive_summary") if isinstance(body.get("executive_summary"), str) else ""
    quality = body.get("quality_report") if isinstance(body.get("quality_report"), dict) else {}
    guardrail = body.get("decision_guardrail") if isinstance(body.get("decision_guardrail"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms"),
        "id": body.get("id"),
        "title": body.get("title"),
        "summary_length": len(summary),
        "direction": body.get("direction"),
        "recommended_action": body.get("recommended_action"),
        "scope": _scope_summary(body),
        "route_identity": _route_identity_summary(body),
        "claims_used": body.get("claims_used"),
        "llm_trace": _llm_trace_summary(body),
        "timing_trace": body.get("timing_trace") if _is_record(body.get("timing_trace")) else None,
        "quality_report_present": _is_record(body.get("quality_report")),
        "decision_guardrail_present": _is_record(body.get("decision_guardrail")),
        "quality_severity": quality.get("severity"),
        "quality_decision_ready": quality.get("decision_ready"),
        "guardrail_status": guardrail.get("status"),
        "guardrail_decision_ready": guardrail.get("decision_ready"),
        "guardrail_effective_action": guardrail.get("effective_action"),
        "guardrail_actionable_action_detected": guardrail.get("actionable_action_detected"),
    }


def _committee_step(result: dict[str, Any]) -> dict[str, Any]:
    body = _body(result)
    synthesis = body.get("synthesis") if isinstance(body.get("synthesis"), str) else ""
    quality = body.get("quality_report") if isinstance(body.get("quality_report"), dict) else {}
    guardrail = body.get("decision_guardrail") if isinstance(body.get("decision_guardrail"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms"),
        "debate_id": body.get("debate_id") or body.get("id"),
        "direction": body.get("direction") or body.get("final_direction"),
        "recommended_action": body.get("recommended_action") or body.get("final_action"),
        "consensus_level": body.get("consensus_level"),
        "synthesis_length": len(synthesis),
        "scope": _scope_summary(body),
        "route_identity": _route_identity_summary(body),
        "llm_trace": _llm_trace_summary(body),
        "timing_trace": body.get("timing_trace") if _is_record(body.get("timing_trace")) else None,
        "decision_guardrail_present": _is_record(body.get("decision_guardrail")),
        "quality_report_present": _is_record(body.get("quality_report")),
        "quality_severity": quality.get("severity"),
        "quality_decision_ready": quality.get("decision_ready"),
        "guardrail_status": guardrail.get("status"),
        "guardrail_decision_ready": guardrail.get("decision_ready"),
        "guardrail_effective_action": guardrail.get("effective_action"),
        "guardrail_actionable_action_detected": guardrail.get("actionable_action_detected"),
    }


def _committee_detail_step(result: dict[str, Any]) -> dict[str, Any]:
    body = _body(result)
    adjudication = body.get("adjudication") if isinstance(body.get("adjudication"), dict) else {}
    synthesis = adjudication.get("synthesis") if isinstance(adjudication.get("synthesis"), str) else ""
    quality = body.get("quality_report") if isinstance(body.get("quality_report"), dict) else {}
    guardrail = body.get("decision_guardrail") if isinstance(body.get("decision_guardrail"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "id": body.get("id"),
        "round_count": len(body.get("rounds") or []) if isinstance(body.get("rounds"), list) else 0,
        "adjudication_synthesis_length": len(synthesis),
        "route_identity": _route_identity_summary(body),
        "timing_trace": body.get("timing_trace") if _is_record(body.get("timing_trace")) else None,
        "decision_guardrail_present": _is_record(body.get("decision_guardrail")),
        "quality_report_present": _is_record(body.get("quality_report")),
        "quality_severity": quality.get("severity"),
        "quality_decision_ready": quality.get("decision_ready"),
        "guardrail_status": guardrail.get("status"),
        "guardrail_decision_ready": guardrail.get("decision_ready"),
        "guardrail_effective_action": guardrail.get("effective_action"),
    }


def _messages_step(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body")
    messages = body if isinstance(body, list) else []
    assistant_payload = next(
        (
            message.get("payload") or {}
            for message in reversed(messages)
            if isinstance(message, dict) and message.get("role") == "assistant"
        ),
        {},
    )
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "count": len(messages),
        "roles": [message.get("role") for message in messages if isinstance(message, dict)],
        "assistant_payload_keys": sorted(assistant_payload.keys()) if isinstance(assistant_payload, dict) else [],
    }


def _trace_step(result: dict[str, Any]) -> dict[str, Any]:
    body = _body(result)
    items = body.get("items") if isinstance(body.get("items"), list) else []
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "count": body.get("count") if isinstance(body.get("count"), int) else len(items),
        "event_types": [item.get("event_type") for item in items if isinstance(item, dict)],
    }


def run_runtime_smoke(
    *,
    base_url: str = "http://127.0.0.1:8000",
    include_committee: bool = False,
    source_id: str | None = None,
    require_real: bool = False,
    timeout: float = 120.0,
) -> dict[str, Any]:
    marker = f"main-flow-smoke-{int(time.time())}"
    report: dict[str, Any] = {
        "report_type": "main_flow_runtime_smoke",
        "generated_at": _utc_now(),
        "base_url": base_url,
        "marker": marker,
        "include_committee": include_committee,
        "selected_source_id": source_id,
        "require_real": require_real,
        "steps": {},
    }
    steps = report["steps"]

    health = _json_request(base_url=base_url, method="GET", path="/api/v1/health", timeout=30)
    steps["health"] = {"ok": health.get("ok"), "status": health.get("status")}

    session = _json_request(
        base_url=base_url,
        method="POST",
        path="/api/v1/chat/sessions",
        payload={"title": f"Runtime Smoke {marker}", "mode": "chat", "asset": "GENERAL"},
        timeout=30,
    )
    session_body = _body(session)
    session_id = session_body.get("id")
    report["session_id"] = session_id
    steps["create_session"] = {
        "ok": bool(session.get("ok")),
        "status": session.get("status"),
        "session_id": session_id,
    }
    if not session_id:
        steps["assessment"] = assess_runtime_smoke_report(
            report,
            require_committee=include_committee,
            require_real=require_real,
        )
        return report

    selected_scope = bool(source_id)
    scope_mode = "selected_sources" if selected_scope else "global"
    source_ids = [source_id] if source_id else []
    chat_question = (
        f"【{marker}】请仅根据当前选定资料，用两段话说明该资料对系统主流程提出了什么要求，"
        "必须给出参考时间、证据边界和下一步建议，并在关键判断后标注引用。"
        if selected_scope
        else f"【{marker}】请用两段话说明当前系统主流程是否适合演示，必须给出参考时间、风险提示和下一步建议。"
    )

    chat = _json_request(
        base_url=base_url,
        method="POST",
        path=f"/api/v1/chat/sessions/{session_id}/chat",
        payload={
            "message": chat_question,
            "history": [],
            "scope_mode": scope_mode,
            "source_ids": source_ids,
        },
        timeout=timeout,
    )
    steps["chat"] = _chat_step(chat)

    messages = _json_request(
        base_url=base_url,
        method="GET",
        path=f"/api/v1/chat/sessions/{session_id}/messages",
        timeout=30,
    )
    steps["messages"] = _messages_step(messages)

    chat_message_id = steps["chat"].get("message_id")
    if chat_message_id:
        trace = _json_request(
            base_url=base_url,
            method="GET",
            path=_query(
                "/api/v1/decision-traces",
                {"related_object_type": "chat_message", "related_object_id": chat_message_id},
            ),
            timeout=30,
        )
        steps["chat_trace"] = _trace_step(trace)

    briefing = _json_request(
        base_url=base_url,
        method="POST",
        path="/api/v1/briefings",
        payload={
            "question": (
                f"【{marker}】请仅根据当前选定资料生成一份简短决策简报：先给结论，再说明证据、风险和下一步。"
                if selected_scope
                else f"【{marker}】请生成一份面向产品演示的 ThinkRoom 主流程 readiness 简报，重点说明是否适合演示、主要风险、下一步。"
            ),
            "asset": "GENERAL",
            "source_ids": source_ids,
        },
        timeout=timeout,
    )
    steps["briefing"] = _briefing_step(briefing)
    briefing_id = steps["briefing"].get("id")
    if briefing_id:
        briefing_trace = _json_request(
            base_url=base_url,
            method="GET",
            path=_query("/api/v1/decision-traces", {"related_object_type": "briefing", "related_object_id": briefing_id}),
            timeout=30,
        )
        steps["briefing_trace"] = _trace_step(briefing_trace)

    if include_committee:
        committee = _json_request(
            base_url=base_url,
            method="POST",
            path="/api/v1/committee/run",
            payload={
                "question": (
                    f"【{marker}】请仅根据当前选定资料审议其对系统主流程提出的要求，并明确证据边界。"
                    if selected_scope
                    else f"【{marker}】从产品演示角度审议：当前系统是否可以对外演示？请保持保守。"
                ),
                "asset": "GENERAL",
                "committee_level": "triple",
                "num_rounds": 1 if selected_scope else 2,
                "source_ids": source_ids,
            },
            timeout=max(timeout, 180),
        )
        steps["committee"] = _committee_step(committee)
        debate_id = steps["committee"].get("debate_id")
        if debate_id:
            detail = _json_request(
                base_url=base_url,
                method="GET",
                path=f"/api/v1/committee/{debate_id}",
                timeout=30,
            )
            steps["committee_detail"] = _committee_detail_step(detail)
            committee_trace = _json_request(
                base_url=base_url,
                method="GET",
                path=_query("/api/v1/decision-traces", {"related_object_type": "debate", "related_object_id": debate_id}),
                timeout=30,
            )
            steps["committee_trace"] = _trace_step(committee_trace)

    steps["assessment"] = assess_runtime_smoke_report(
        report,
        require_committee=include_committee,
        require_real=require_real,
    )
    return report


def _has_required_events(step: dict[str, Any], required: set[str], prefix: str, errors: list[str]) -> None:
    event_types = set(step.get("event_types") or [])
    for event_type in sorted(required):
        if event_type not in event_types:
            errors.append(f"{prefix}.missing_event:{event_type}")


def _validate_real_llm_calls(step: dict[str, Any], prefix: str, errors: list[str]) -> None:
    trace = step.get("llm_trace") if isinstance(step.get("llm_trace"), dict) else {}
    calls = trace.get("calls") if isinstance(trace.get("calls"), list) else []
    if int(trace.get("call_count") or len(calls)) <= 0 or not calls:
        errors.append(f"{prefix}.real_llm_call_missing")
        return
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            errors.append(f"{prefix}.call_{index}.invalid")
            continue
        if call.get("mock") is not False:
            errors.append(f"{prefix}.call_{index}.not_real")
        if not call.get("model"):
            errors.append(f"{prefix}.call_{index}.model_missing")
        if not call.get("requested_model"):
            errors.append(f"{prefix}.call_{index}.requested_model_missing")
        if not isinstance(call.get("configured_fallback_models"), list):
            errors.append(f"{prefix}.call_{index}.configured_fallback_models_missing")
        if call.get("cost_usd") is None:
            errors.append(f"{prefix}.call_{index}.cost_missing")
        if call.get("cost_source") not in {"provider_usage", "static_estimate"}:
            errors.append(f"{prefix}.call_{index}.cost_source_unavailable")
        if not call.get("generation_id"):
            errors.append(f"{prefix}.call_{index}.generation_id_missing")


def _validate_route_identity(
    step: dict[str, Any],
    prefix: str,
    expected_scope: str,
    errors: list[str],
) -> None:
    identity = step.get("route_identity") if isinstance(step.get("route_identity"), dict) else None
    if identity is None:
        errors.append(f"{prefix}.route_identity_missing")
        return
    if identity.get("scope") != expected_scope:
        errors.append(f"{prefix}.route_identity_scope_mismatch")
    for field in ("fingerprint", "configuration_fingerprint", "implementation_fingerprint"):
        value = identity.get(field)
        if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
            errors.append(f"{prefix}.route_identity_{field}_invalid")


def _validate_ready_structured_output(step: dict[str, Any], prefix: str, errors: list[str]) -> None:
    if step.get("quality_severity") != "ok":
        errors.append(f"{prefix}.quality_not_ok")
    if step.get("quality_decision_ready") is not True:
        errors.append(f"{prefix}.quality_not_decision_ready")
    if step.get("guardrail_status") != "passed":
        errors.append(f"{prefix}.guardrail_not_passed")
    if step.get("guardrail_decision_ready") is not True:
        errors.append(f"{prefix}.guardrail_not_decision_ready")


def _validate_committee_quality_boundary(step: dict[str, Any], errors: list[str]) -> None:
    if not step.get("quality_report_present"):
        errors.append("committee.quality_report_missing")
        return

    ready = (
        step.get("quality_severity") == "ok"
        and step.get("quality_decision_ready") is True
        and step.get("guardrail_status") == "passed"
        and step.get("guardrail_decision_ready") is True
    )
    safe_boundary = (
        step.get("quality_severity") == "warning"
        and step.get("quality_decision_ready") is False
        and step.get("guardrail_status") == "warn_only"
        and step.get("guardrail_decision_ready") is False
        and step.get("guardrail_effective_action") in {"watch", "hold", "avoid"}
        and step.get("guardrail_actionable_action_detected") is False
    )
    if not ready and not safe_boundary:
        errors.append("committee.quality_boundary_unsafe_or_inconsistent")


def assess_runtime_smoke_report(
    report: dict[str, Any],
    *,
    require_committee: bool = False,
    require_real: bool = False,
) -> dict[str, Any]:
    steps = report.get("steps") if isinstance(report.get("steps"), dict) else {}
    errors: list[str] = []
    warnings: list[str] = []

    health = steps.get("health") if isinstance(steps.get("health"), dict) else {}
    if not health.get("ok") or health.get("status") != 200:
        errors.append("health.failed")

    create_session = steps.get("create_session") if isinstance(steps.get("create_session"), dict) else {}
    if not create_session.get("ok") or not create_session.get("session_id"):
        errors.append("create_session.failed")

    chat = steps.get("chat") if isinstance(steps.get("chat"), dict) else {}
    if not chat.get("ok") or chat.get("status") != 200:
        errors.append("chat.request_failed")
    if int(chat.get("content_length") or 0) < 80:
        errors.append("chat.content_length_too_short")
    if not chat.get("message_id"):
        errors.append("chat.message_id_missing")
    if not chat.get("reference_time_present"):
        errors.append("chat.reference_time_missing")
    if not chat.get("quality_report_present"):
        errors.append("chat.quality_report_missing")
    if not chat.get("decision_guardrail_present"):
        errors.append("chat.decision_guardrail_missing")
    if chat.get("guardrail_status") == "blocked_output":
        errors.append("chat.output_blocked")
    chat_timing = chat.get("timing_trace") if isinstance(chat.get("timing_trace"), dict) else {}
    if (
        int(chat_timing.get("total_ms") or 0) <= 0
        or "retrieval_ms" not in chat_timing
        or "model_calls_ms" not in chat_timing
    ):
        errors.append("chat.timing_trace_missing")
    if int(chat.get("citations_count") or 0) <= 0:
        warnings.append("chat.no_citations_returned")
        if not chat.get("visible_caution_present"):
            errors.append("chat.uncited_answer_lacks_visible_caution")

    selected_source_id = report.get("selected_source_id")
    if isinstance(selected_source_id, str) and selected_source_id:
        for step_name, step in (("chat", chat),):
            scope = step.get("scope") if isinstance(step.get("scope"), dict) else {}
            if scope.get("mode") != "selected_sources" or selected_source_id not in (scope.get("source_ids") or []):
                errors.append(f"{step_name}.selected_source_scope_missing")
        if selected_source_id not in (chat.get("citation_source_ids") or []):
            errors.append("chat.selected_source_citation_missing")

    messages = steps.get("messages") if isinstance(steps.get("messages"), dict) else {}
    if int(messages.get("count") or 0) < 2:
        errors.append("messages.assistant_message_missing")
    if "assistant" not in (messages.get("roles") or []):
        errors.append("messages.assistant_message_missing")
    payload_keys = set(messages.get("assistant_payload_keys") or [])
    for key in ("quality_report", "decision_guardrail", "run_id", "timing_trace"):
        if key not in payload_keys:
            errors.append(f"messages.assistant_payload_missing:{key}")

    chat_trace = steps.get("chat_trace") if isinstance(steps.get("chat_trace"), dict) else {}
    if int(chat_trace.get("count") or 0) <= 0:
        errors.append("chat_trace.empty")
    _has_required_events(chat_trace, REQUIRED_CHAT_TRACE_EVENTS, "chat_trace", errors)

    briefing = steps.get("briefing") if isinstance(steps.get("briefing"), dict) else {}
    if not briefing.get("ok") or briefing.get("status") != 200:
        errors.append("briefing.request_failed")
    if not briefing.get("id"):
        errors.append("briefing.id_missing")
    if int(briefing.get("summary_length") or 0) < 20:
        errors.append("briefing.summary_too_short")
    if not briefing.get("quality_report_present"):
        errors.append("briefing.quality_report_missing")
    if not briefing.get("decision_guardrail_present"):
        errors.append("briefing.decision_guardrail_missing")
    briefing_timing = (
        briefing.get("timing_trace") if isinstance(briefing.get("timing_trace"), dict) else {}
    )
    if (
        int(briefing_timing.get("total_ms") or 0) <= 0
        or "retrieval_ms" not in briefing_timing
        or "model_calls_ms" not in briefing_timing
    ):
        errors.append("briefing.timing_trace_missing")
    if isinstance(selected_source_id, str) and selected_source_id:
        briefing_scope = briefing.get("scope") if isinstance(briefing.get("scope"), dict) else {}
        if briefing_scope.get("mode") != "selected_sources" or selected_source_id not in (briefing_scope.get("source_ids") or []):
            errors.append("briefing.selected_source_scope_missing")
        if int(briefing.get("claims_used") or 0) <= 0:
            errors.append("briefing.selected_source_claim_missing")

    briefing_trace = steps.get("briefing_trace") if isinstance(steps.get("briefing_trace"), dict) else {}
    if int(briefing_trace.get("count") or 0) <= 0:
        errors.append("briefing_trace.empty")
    _has_required_events(briefing_trace, REQUIRED_STRUCTURED_TRACE_EVENTS, "briefing_trace", errors)

    if require_committee:
        committee = steps.get("committee") if isinstance(steps.get("committee"), dict) else {}
        if not committee.get("ok") or committee.get("status") != 200:
            errors.append("committee.request_failed")
        if not committee.get("debate_id"):
            errors.append("committee.debate_id_missing")
        if int(committee.get("synthesis_length") or 0) < 80:
            errors.append("committee.synthesis_too_short")
        if not committee.get("decision_guardrail_present"):
            errors.append("committee.decision_guardrail_missing")
        committee_timing = committee.get("timing_trace") if isinstance(committee.get("timing_trace"), dict) else {}
        if int(committee_timing.get("total_ms") or 0) <= 0:
            errors.append("committee.timing_trace_missing")
        if isinstance(selected_source_id, str) and selected_source_id:
            committee_scope = committee.get("scope") if isinstance(committee.get("scope"), dict) else {}
            if committee_scope.get("mode") != "selected_sources" or selected_source_id not in (committee_scope.get("source_ids") or []):
                errors.append("committee.selected_source_scope_missing")

        detail = steps.get("committee_detail") if isinstance(steps.get("committee_detail"), dict) else {}
        if not detail.get("ok") or detail.get("status") != 200:
            errors.append("committee_detail.request_failed")
        if int(detail.get("round_count") or 0) <= 0:
            errors.append("committee_detail.rounds_missing")
        if int(detail.get("adjudication_synthesis_length") or 0) < 80:
            errors.append("committee_detail.adjudication_too_short")
        detail_timing = detail.get("timing_trace") if isinstance(detail.get("timing_trace"), dict) else {}
        if int(detail_timing.get("total_ms") or 0) <= 0:
            errors.append("committee_detail.timing_trace_missing")

        committee_trace = steps.get("committee_trace") if isinstance(steps.get("committee_trace"), dict) else {}
        if int(committee_trace.get("count") or 0) <= 0:
            errors.append("committee_trace.empty")
        _has_required_events(committee_trace, REQUIRED_CHAT_TRACE_EVENTS, "committee_trace", errors)

    if require_real:
        _validate_route_identity(chat, "chat", "decision_chat_selected_source", errors)
        _validate_route_identity(briefing, "briefing", "briefing_selected_source", errors)
        _validate_real_llm_calls(chat, "chat", errors)
        if chat.get("guardrail_status") != "passed":
            errors.append("chat.guardrail_not_passed")
        if chat.get("quality_severity") != "ok":
            errors.append("chat.quality_not_ok")
        if chat.get("quality_decision_ready") is not True:
            errors.append("chat.quality_not_decision_ready")
        if chat.get("guardrail_decision_ready") is not True:
            errors.append("chat.guardrail_not_decision_ready")
        _validate_real_llm_calls(briefing, "briefing", errors)
        _validate_ready_structured_output(briefing, "briefing", errors)
        if require_committee:
            _validate_route_identity(committee, "committee", "committee_selected_source", errors)
            detail = steps.get("committee_detail") if isinstance(steps.get("committee_detail"), dict) else {}
            _validate_route_identity(detail, "committee_detail", "committee_selected_source", errors)
            committee_identity = committee.get("route_identity") if isinstance(committee.get("route_identity"), dict) else {}
            detail_identity = detail.get("route_identity") if isinstance(detail.get("route_identity"), dict) else {}
            if (
                committee_identity.get("fingerprint")
                and detail_identity.get("fingerprint")
                and committee_identity.get("fingerprint") != detail_identity.get("fingerprint")
            ):
                errors.append("committee_detail.route_identity_mismatch")
            _validate_real_llm_calls(committee, "committee", errors)
            _validate_committee_quality_boundary(committee, errors)
            if not detail.get("quality_report_present"):
                errors.append("committee_detail.quality_report_missing")
            if not detail.get("decision_guardrail_present"):
                errors.append("committee_detail.decision_guardrail_missing")
            for field in (
                "quality_severity",
                "quality_decision_ready",
                "guardrail_status",
                "guardrail_decision_ready",
                "guardrail_effective_action",
            ):
                if detail.get(field) != committee.get(field):
                    errors.append(f"committee_detail.{field}_mismatch")

    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "warnings": warnings,
        "checked_at": _utc_now(),
        "require_committee": require_committee,
        "require_real": require_real,
    }


def write_report(report: dict[str, Any], output_path: str | Path | None = None) -> Path:
    resolved = Path(output_path) if output_path else default_output_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    resolved.write_text(payload, encoding="utf-8")
    latest = latest_output_path(resolved)
    if latest != resolved:
        latest.write_text(payload, encoding="utf-8")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or assess a live main-flow runtime smoke report.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default=None)
    parser.add_argument("--input", default=None, help="Assess an existing report instead of running live HTTP calls.")
    parser.add_argument("--include-committee", action="store_true")
    parser.add_argument("--source-id", default=None, help="Restrict all modes to one existing source id.")
    parser.add_argument("--require-real", action="store_true", help="Require auditable non-mock model calls in every mode.")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)

    if args.input:
        report = json.loads(Path(args.input).read_text(encoding="utf-8"))
        report["assessment"] = assess_runtime_smoke_report(
            report,
            require_committee=args.include_committee,
            require_real=args.require_real,
        )
    else:
        report = run_runtime_smoke(
            base_url=args.base_url,
            include_committee=args.include_committee,
            source_id=args.source_id,
            require_real=args.require_real,
            timeout=args.timeout,
        )
    output = write_report(report, args.output)
    assessment = report.get("assessment") or report.get("steps", {}).get("assessment")
    print(json.dumps({"output_path": str(output), "assessment": assessment}, ensure_ascii=False, indent=2))
    return 0 if isinstance(assessment, dict) and assessment.get("status") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
