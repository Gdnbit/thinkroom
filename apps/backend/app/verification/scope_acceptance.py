"""Acceptance checks for scoped chat retrieval.

This module intentionally keeps the test narrow:
- create two marked sources and claims;
- verify selected_sources retrieval only returns the selected source;
- verify chat context includes the selected source block;
- verify the chat, briefing, and committee APIs echo selected scope metadata
  without requiring a real LLM call.

It does not delete test records. Repeated runs create append-only acceptance
records so historical validation evidence is preserved.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from ulid import ULID

from app.config import settings
from app.database import safe_commit
from app.dependencies import SessionLocal
from app.main import app
from app.schemas.chat import ChatSession
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.decision_trace_event import DecisionTraceEvent
from app.schemas.raw_source import RawSource
from app.services import semantic_search
from app.services.chat_harness import build_chat_context


def _new_id() -> str:
    return str(ULID())


def _make_claim(
    *,
    claim_id: str,
    source_id: str,
    marker: str,
    summary: str,
    created_at: datetime,
) -> ClaimCard:
    claim = ClaimCard(
        id=claim_id,
        summary=f"{marker} {summary}",
        full_quote=f"{marker} 原文证据：{summary}。",
        contextual_text=f"{marker} 范围检索上下文：{summary}。",
        claim_type="risk_warning",
        stance="neutral",
        time_horizon="short",
        asset_scope=[],
        theme_scope=["scope_acceptance"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=[],
        confidence=0.9,
        source_trust_weight=0.8,
        is_contradicted=False,
        status="active",
        time_sensitive=False,
        version=1,
        created_at=created_at,
        updated_at=created_at,
    )
    claim.raw_source_links.append(
        ClaimRawSourceLink(
            claim_id=claim_id,
            source_id=source_id,
            link_type="acceptance_seed",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    return claim


def _seed_scope_fixture(db) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    run_id = _new_id()
    marker_a = f"SCOPE_ACCEPTANCE_ALPHA_{run_id}"
    marker_b = f"SCOPE_ACCEPTANCE_BETA_{run_id}"
    source_a_id = _new_id()
    source_b_id = _new_id()
    claim_a_id = _new_id()
    claim_b_id = _new_id()

    source_a = RawSource(
        id=source_a_id,
        source_type="note",
        title=f"scope acceptance selected source {run_id}",
        raw_text=(
            f"{marker_a} 当前选定资料要求系统主流程仅使用用户明确选择的信息源回答，"
            "在关键判断后标注引用，并在证据不足时明确说明边界。"
        ),
        article_summary=(
            f"{marker_a} 仅使用选定信息源，关键判断标注引用，证据不足时说明边界。"
        ),
        language="zh",
        status="completed",
        word_count=12,
        created_at=now,
        updated_at=now,
    )
    source_b = RawSource(
        id=source_b_id,
        source_type="note",
        title=f"scope acceptance excluded source {run_id}",
        raw_text=f"{marker_b} 该资料未被用户选中，不得用于本次回答。",
        article_summary=f"{marker_b} 未选资料摘要，不得引用。",
        language="zh",
        status="completed",
        word_count=12,
        created_at=now,
        updated_at=now,
    )

    db.add(source_a)
    db.add(source_b)
    db.add(
        _make_claim(
            claim_id=claim_a_id,
            source_id=source_a_id,
            marker=marker_a,
            summary="系统主流程仅使用用户选定的信息源回答，关键判断标注引用，证据不足时说明边界",
            created_at=now,
        )
    )
    db.add(
        _make_claim(
            claim_id=claim_b_id,
            source_id=source_b_id,
            marker=marker_b,
            summary="未选资料不得用于本次回答",
            created_at=now,
        )
    )
    safe_commit(db)
    return {
        "run_id": run_id,
        "marker_a": marker_a,
        "marker_b": marker_b,
        "source_a_id": source_a_id,
        "source_b_id": source_b_id,
        "claim_a_id": claim_a_id,
        "claim_b_id": claim_b_id,
    }


def _assert(name: str, condition: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": "passed" if condition else "failed",
        "details": details or {},
    }


def _trace_summary(db, related_object_type: str, related_object_id: str) -> dict[str, Any]:
    rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == related_object_type,
            DecisionTraceEvent.related_object_id == related_object_id,
        )
        .all()
    )
    return {
        "related_object_type": related_object_type,
        "related_object_id": related_object_id,
        "count": len(rows),
        "event_types": sorted({row.event_type for row in rows}),
        "llm_call_count": sum(1 for row in rows if row.event_type == "llm_call"),
        "source_ids": sorted({
            source_id
            for row in rows
            for source_id in (row.source_ids or [])
            if isinstance(source_id, str)
        }),
    }


def _standard_trace_ok(summary: dict[str, Any], expected_source_id: str, min_llm_calls: int = 1) -> bool:
    event_types = set(summary.get("event_types") or [])
    return (
        {"retrieval", "scope", "quality", "guardrail", "llm_call"}.issubset(event_types)
        and summary.get("llm_call_count", 0) >= min_llm_calls
        and summary.get("source_ids") == [expected_source_id]
    )


def run_acceptance(write_report: bool = True) -> dict[str, Any]:
    old_mock = getattr(settings, "OPENROUTER_MOCK", False)
    old_embed = semantic_search.embed_text
    settings.OPENROUTER_MOCK = True
    semantic_search.embed_text = lambda _text: None

    assertions: list[dict[str, Any]] = []
    db = SessionLocal()
    try:
        fixture = _seed_scope_fixture(db)

        scoped_results = semantic_search.semantic_search(
            db=db,
            query=fixture["marker_a"],
            limit=10,
            source_ids=[fixture["source_a_id"]],
            status_filter="active",
        )
        scoped_ids = [c.id for c in scoped_results]
        assertions.append(
            _assert(
                "semantic_search_selected_source_only",
                fixture["claim_a_id"] in scoped_ids and fixture["claim_b_id"] not in scoped_ids,
                {"returned_claim_ids": scoped_ids},
            )
        )

        global_results = semantic_search.semantic_search(
            db=db,
            query=fixture["marker_a"],
            limit=10,
            status_filter="active",
        )
        global_ids = [c.id for c in global_results]
        assertions.append(
            _assert(
                "semantic_search_global_can_find_fixture",
                fixture["claim_a_id"] in global_ids,
                {"returned_claim_ids": global_ids},
            )
        )

        ctx = build_chat_context(
            db=db,
            query=fixture["marker_a"],
            source_ids=[fixture["source_a_id"]],
            include_market=False,
        )
        source_scope = getattr(ctx, "source_scope", {})
        assertions.append(
            _assert(
                "chat_context_scope_metadata",
                source_scope.get("matched_source_ids") == [fixture["source_a_id"]],
                {"source_scope": source_scope},
            )
        )
        assertions.append(
            _assert(
                "chat_context_includes_selected_not_excluded",
                fixture["marker_a"] in ctx.context_block and fixture["marker_b"] not in ctx.context_block,
                {"context_preview": ctx.context_block[:500]},
            )
        )

        client = TestClient(app)
        session_payload = client.post(
            "/api/v1/chat/sessions",
            json={"title": f"scope acceptance {fixture['run_id']}", "mode": "auto"},
        )
        session_payload.raise_for_status()
        session_id = session_payload.json()["id"]
        chat_payload = client.post(
            f"/api/v1/chat/sessions/{session_id}/chat",
            json={
                "message": fixture["marker_a"],
                "history": [],
                "scope_mode": "selected_sources",
                "source_ids": [fixture["source_a_id"]],
            },
        )
        chat_payload.raise_for_status()
        chat_data = chat_payload.json()
        scope = chat_data.get("scope") or {}
        chat_citations = chat_data.get("citations") or []
        chat_citation_text = json.dumps(chat_citations, ensure_ascii=False)
        assertions.append(
            _assert(
                "chat_api_returns_selected_scope",
                scope.get("mode") == "selected_sources"
                and scope.get("source_ids") == [fixture["source_a_id"]]
                and scope.get("source_scope", {}).get("matched_source_ids") == [fixture["source_a_id"]],
                {"scope": scope},
            )
        )
        assertions.append(
            _assert(
                "chat_evidence_payload_ready",
                bool(chat_citations)
                and fixture["marker_a"] in chat_citation_text
                and fixture["marker_b"] not in chat_citation_text
                and any(
                    citation.get("source_id") == fixture["source_a_id"]
                    for citation in chat_citations
                    if isinstance(citation, dict)
                ),
                {"citations": chat_citations[:3]},
            )
        )
        chat_timing_trace = chat_data.get("timing_trace") or {}
        assertions.append(
            _assert(
                "chat_timing_trace_visible",
                isinstance(chat_timing_trace, dict)
                and int(chat_timing_trace.get("total_ms") or 0) > 0
                and int(chat_timing_trace.get("retrieval_ms") or 0) >= 0
                and int(chat_timing_trace.get("model_calls_ms") or 0) >= 0,
                {"timing_trace": chat_timing_trace},
            )
        )

        briefing_payload = client.post(
            "/api/v1/briefings",
            json={
                "question": fixture["marker_a"],
                "asset_scope": ["GENERAL"],
                "source_ids": [fixture["source_a_id"], fixture["source_a_id"], ""],
                "committee_level": "single",
            },
        )
        briefing_payload.raise_for_status()
        briefing_data = briefing_payload.json()
        briefing_scope = briefing_data.get("scope") or {}
        briefing_data_sources = briefing_data.get("data_sources") or {}
        briefing_claims_block = (briefing_data_sources.get("claims") or {})
        briefing_events_block = (briefing_data_sources.get("events") or {})
        briefing_claim_items = briefing_claims_block.get("items") or []
        briefing_claim_text = json.dumps(briefing_claim_items, ensure_ascii=False)
        assertions.append(
            _assert(
                "briefing_api_returns_selected_scope",
                briefing_scope.get("mode") == "selected_sources"
                and briefing_scope.get("source_ids") == [fixture["source_a_id"]]
                and briefing_scope.get("asset") == "GENERAL",
                {"scope": briefing_scope},
            )
        )
        assertions.append(
            _assert(
                "briefing_uses_selected_not_excluded_claim",
                fixture["marker_a"] in briefing_claim_text
                and fixture["marker_b"] not in briefing_claim_text,
                {"claims": briefing_claim_items},
            )
        )
        assertions.append(
            _assert(
                "briefing_evidence_payload_ready",
                isinstance(briefing_data_sources, dict)
                and int(briefing_claims_block.get("count") or 0) >= 1
                and fixture["marker_a"] in briefing_claim_text
                and fixture["marker_b"] not in briefing_claim_text
                and isinstance(briefing_events_block, dict),
                {"data_sources": briefing_data_sources},
            )
        )
        briefing_timing_trace = briefing_data.get("timing_trace") or {}
        assertions.append(
            _assert(
                "briefing_timing_trace_visible",
                isinstance(briefing_timing_trace, dict)
                and int(briefing_timing_trace.get("total_ms") or 0) > 0
                and int(briefing_timing_trace.get("retrieval_ms") or 0) >= 0
                and int(briefing_timing_trace.get("model_calls_ms") or 0) >= 0,
                {"timing_trace": briefing_timing_trace},
            )
        )
        briefing_user_payload = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={
                "role": "user",
                "content": f"{fixture['marker_a']} briefing acceptance prompt",
                "payload": {"mode": "briefing"},
            },
        )
        briefing_user_payload.raise_for_status()
        briefing_message_payload = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={
                "role": "assistant",
                "content": (
                    f"截至 {datetime.now(timezone.utc).isoformat()}，简报结论："
                    f"{fixture['marker_a']} selected claim can be shown as evidence."
                ),
                "payload": {
                    "mode": "briefing",
                    "briefing_id": briefing_data.get("id"),
                    "data_sources": briefing_data_sources,
                    "decision_guardrail": briefing_data.get("decision_guardrail"),
                    "llm_trace": briefing_data.get("llm_trace"),
                    "timing_trace": briefing_data.get("timing_trace"),
                    "scope": briefing_data.get("scope"),
                    "model_used": briefing_data.get("model_used"),
                    "duration_ms": briefing_data.get("duration_ms"),
                },
            },
        )
        briefing_message_payload.raise_for_status()

        committee_payload = client.post(
            "/api/v1/committee/run",
            json={
                "question": fixture["marker_a"],
                "asset": "GENERAL",
                "num_rounds": 1,
                "source_ids": [fixture["source_a_id"], fixture["source_a_id"], ""],
            },
        )
        committee_payload.raise_for_status()
        committee_data = committee_payload.json()
        committee_scope = committee_data.get("scope") or {}
        committee_data_sources = committee_data.get("data_sources") or {}
        committee_claims_block = (committee_data_sources.get("claims") or {})
        committee_events_block = (committee_data_sources.get("events") or {})
        committee_claim_items = committee_claims_block.get("items") or []
        committee_claim_text = json.dumps(committee_claim_items, ensure_ascii=False)
        assertions.append(
            _assert(
                "committee_api_returns_selected_scope",
                committee_data.get("success") is True
                and committee_scope.get("mode") == "selected_sources"
                and committee_scope.get("source_ids") == [fixture["source_a_id"]],
                {
                    "scope": committee_scope,
                    "success": committee_data.get("success"),
                    "activated_seats": committee_data.get("activated_seats"),
                },
            )
        )
        assertions.append(
            _assert(
                "committee_evidence_payload_ready",
                isinstance(committee_data_sources, dict)
                and int(committee_claims_block.get("count") or 0) >= 1
                and fixture["marker_a"] in committee_claim_text
                and fixture["marker_b"] not in committee_claim_text
                and isinstance(committee_events_block, dict),
                {"data_sources": committee_data_sources},
            )
        )
        assertions.append(
            _assert(
                "committee_trace_visible",
                isinstance(committee_data.get("llm_trace"), dict)
                and int((committee_data.get("llm_trace") or {}).get("call_count") or 0) >= 1
                and isinstance(committee_data.get("decision_guardrail"), dict),
                {
                    "llm_trace": committee_data.get("llm_trace"),
                    "decision_guardrail": committee_data.get("decision_guardrail"),
                },
            )
        )
        committee_timing_trace = committee_data.get("timing_trace") or {}
        assertions.append(
            _assert(
                "committee_timing_trace_visible",
                isinstance(committee_timing_trace, dict)
                and int(committee_timing_trace.get("total_ms") or 0) > 0
                and int(committee_timing_trace.get("role_retrieval_ms") or 0) >= 0
                and int(committee_timing_trace.get("model_calls_ms") or 0) >= 0,
                {"timing_trace": committee_timing_trace},
            )
        )
        committee_user_payload = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={
                "role": "user",
                "content": f"{fixture['marker_a']} committee acceptance prompt",
                "payload": {"mode": "debate"},
            },
        )
        committee_user_payload.raise_for_status()
        committee_message_payload = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={
                "role": "assistant",
                "content": (
                    f"截至 {datetime.now(timezone.utc).isoformat()}，委员会裁决："
                    f"{fixture['marker_a']} selected claim remains visible in the decision thread."
                ),
                "payload": {
                    "mode": "debate",
                    "debate_id": committee_data.get("debate_id"),
                    "data_sources": committee_data_sources,
                    "decision_guardrail": committee_data.get("decision_guardrail"),
                    "llm_trace": committee_data.get("llm_trace"),
                    "timing_trace": committee_data.get("timing_trace"),
                    "scope": committee_data.get("scope"),
                    "activated_seats": committee_data.get("activated_seats"),
                    "duration_ms": committee_data.get("duration_ms"),
                },
            },
        )
        committee_message_payload.raise_for_status()
        ui_messages_payload = client.get(f"/api/v1/chat/sessions/{session_id}/messages")
        ui_messages_payload.raise_for_status()
        ui_messages = ui_messages_payload.json()
        assistant_modes = [
            ((message.get("payload") or {}).get("mode") or "chat")
            for message in ui_messages
            if message.get("role") == "assistant"
        ]
        assertions.append(
            _assert(
                "ui_chat_session_contains_chat_briefing_committee_messages",
                {"chat", "briefing", "debate"}.issubset(set(assistant_modes)),
                {
                    "session_id": session_id,
                    "message_count": len(ui_messages),
                    "assistant_modes": assistant_modes,
                },
            )
        )

        chat_trace = _trace_summary(db, "chat_message", chat_data.get("id") or "")
        briefing_trace = _trace_summary(db, "briefing", briefing_data.get("id") or "")
        committee_trace = _trace_summary(db, "debate", committee_data.get("debate_id") or "")
        assertions.append(
            _assert(
                "chat_decision_trace_persisted",
                _standard_trace_ok(chat_trace, fixture["source_a_id"]),
                chat_trace,
            )
        )
        assertions.append(
            _assert(
                "briefing_decision_trace_persisted",
                _standard_trace_ok(
                    briefing_trace,
                    fixture["source_a_id"],
                    min_llm_calls=max(1, int((briefing_data.get("llm_trace") or {}).get("call_count") or 1)),
                ),
                briefing_trace,
            )
        )
        assertions.append(
            _assert(
                "committee_decision_trace_persisted",
                _standard_trace_ok(
                    committee_trace,
                    fixture["source_a_id"],
                    min_llm_calls=max(1, int((committee_data.get("llm_trace") or {}).get("call_count") or 1)),
                ),
                committee_trace,
            )
        )

        # Make sure the created session is visible in DB for auditability.
        persisted_session = db.get(ChatSession, session_id)
        assertions.append(
            _assert(
                "chat_session_persisted",
                persisted_session is not None,
                {"session_id": session_id},
            )
        )

        status = "passed" if all(a["status"] == "passed" for a in assertions) else "failed"
        report = {
            "status": status,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "fixture": fixture,
            "assertions": assertions,
            "snapshots": {
                "ui_session": {
                    "session_id": session_id,
                    "message_count": len(ui_messages),
                    "assistant_modes": assistant_modes,
                },
                "chat": {
                    "id": chat_data.get("id"),
                    "claims_used": chat_data.get("claims_used"),
                    "scope": chat_data.get("scope"),
                    "citations": chat_data.get("citations"),
                    "evidence_payload_ready": bool(chat_citations),
                    "content_preview": (chat_data.get("content") or "")[:500],
                    "decision_trace": chat_trace,
                    "timing_trace": chat_data.get("timing_trace"),
                },
                "briefing": {
                    "id": briefing_data.get("id"),
                    "scope": briefing_data.get("scope"),
                    "claims": briefing_claim_items,
                    "data_sources": briefing_data_sources,
                    "guardrail": briefing_data.get("decision_guardrail"),
                    "llm_trace": briefing_data.get("llm_trace"),
                    "timing_trace": briefing_data.get("timing_trace"),
                    "decision_trace": briefing_trace,
                },
                "committee": {
                    "debate_id": committee_data.get("debate_id"),
                    "scope": committee_data.get("scope"),
                    "data_sources": committee_data_sources,
                    "activated_seats": committee_data.get("activated_seats"),
                    "guardrail": committee_data.get("decision_guardrail"),
                    "llm_trace": committee_data.get("llm_trace"),
                    "timing_trace": committee_data.get("timing_trace"),
                    "decision_trace": committee_trace,
                },
            },
            "notes": [
                "OPENROUTER_MOCK was enabled for this acceptance run.",
                "The run validates scope isolation and request plumbing across chat, briefing, and committee; it does not validate real model quality.",
                "Created fixture records are preserved for audit and are not deleted.",
            ],
        }

        if write_report:
            root = Path(__file__).resolve().parents[4]
            report_dir = root / "docs" / "verification"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"scope_acceptance_{fixture['run_id']}.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            report["report_path"] = str(report_path)

        return report
    finally:
        db.close()
        settings.OPENROUTER_MOCK = old_mock
        semantic_search.embed_text = old_embed


if __name__ == "__main__":
    result = run_acceptance(write_report=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["status"] == "passed" else 1)
