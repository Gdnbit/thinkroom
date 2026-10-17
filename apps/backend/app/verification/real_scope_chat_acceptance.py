"""Real LLM acceptance check for scoped chat.

Run this only after a real OpenRouter key is available in the process
environment. The script deliberately refuses to run in mock mode and writes a
JSON report under docs/verification.

It preserves all created fixture records for auditability.
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
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.raw_source import RawSource
from app.services import semantic_search
from app.utils.llm_client import llm_client


def _new_id() -> str:
    return str(ULID())


def _assert(name: str, condition: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "passed" if condition else "failed", "details": details or {}}


def _seed_fixture(db) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    run_id = _new_id()
    marker_a = f"REAL_SCOPE_ALPHA_{run_id}"
    marker_b = f"REAL_SCOPE_BETA_{run_id}"
    source_a_id = _new_id()
    source_b_id = _new_id()
    claim_a_id = _new_id()
    claim_b_id = _new_id()

    db.add(
        RawSource(
            id=source_a_id,
            source_type="note",
            title=f"real scope selected source {run_id}",
            raw_text=(
                f"{marker_a} The selected document says the MVP decision system "
                "should prioritize scoped retrieval, source transparency, and "
                "evidence-grounded answers before adding graph reasoning."
            ),
            article_summary=(
                f"{marker_a} selected source summary: prioritize scoped retrieval, "
                "source transparency, and evidence-grounded answers."
            ),
            language="en",
            status="completed",
            word_count=24,
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        RawSource(
            id=source_b_id,
            source_type="note",
            title=f"real scope excluded source {run_id}",
            raw_text=f"{marker_b} Excluded material says something unrelated and must not be used.",
            article_summary=f"{marker_b} excluded source summary.",
            language="en",
            status="completed",
            word_count=12,
            created_at=now,
            updated_at=now,
        )
    )

    for claim_id, source_id, marker, summary in [
        (
            claim_a_id,
            source_a_id,
            marker_a,
            "The MVP should prioritize scoped retrieval, source transparency, and evidence-grounded answers before graph reasoning.",
        ),
        (
            claim_b_id,
            source_b_id,
            marker_b,
            "This excluded claim should not be used by selected_sources chat.",
        ),
    ]:
        claim = ClaimCard(
            id=claim_id,
            summary=f"{marker} {summary}",
            full_quote=f"{marker} evidence quote: {summary}",
            contextual_text=f"{marker} scoped acceptance context.",
            claim_type="strategy_rule",
            stance="neutral",
            time_horizon="short",
            asset_scope=[],
            theme_scope=["real_scope_acceptance"],
            geographic_scope=[],
            conditions=[],
            invalidation_conditions=[],
            reasoning_basis=[],
            confidence=0.9,
            source_trust_weight=0.85,
            is_contradicted=False,
            status="active",
            time_sensitive=False,
            version=1,
            created_at=now,
            updated_at=now,
        )
        claim.raw_source_links.append(
            ClaimRawSourceLink(
                claim_id=claim_id,
                source_id=source_id,
                link_type="acceptance_seed",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(claim)

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


def run_acceptance(write_report: bool = True) -> dict[str, Any]:
    if getattr(settings, "OPENROUTER_MOCK", False):
        raise RuntimeError("OPENROUTER_MOCK is enabled; real acceptance requires real model calls.")
    if not settings.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured in process environment.")

    old_embed = semantic_search.embed_text
    semantic_search.embed_text = lambda _text: None
    db = SessionLocal()
    assertions: list[dict[str, Any]] = []
    fixture: dict[str, str] = {}
    chat_data: dict[str, Any] = {}
    ping: dict[str, Any] = {}
    try:
        ping = llm_client.ping_model("fusion", timeout_s=20)
        assertions.append(_assert("fusion_model_ping", bool(ping.get("success")), {"ping": ping}))
        if not ping.get("success"):
            raise RuntimeError(f"fusion ping failed: {ping.get('error')}")

        fixture = _seed_fixture(db)
        client = TestClient(app)
        session = client.post(
            "/api/v1/chat/sessions",
            json={"title": f"real scoped chat {fixture['run_id']}", "mode": "auto"},
        )
        session.raise_for_status()
        session_id = session.json()["id"]

        prompt = (
            "请只根据当前选定资料回答：这个测试资料要求 MVP 优先做什么？"
            "回答要简短，并引用可用编号。"
        )
        response = client.post(
            f"/api/v1/chat/sessions/{session_id}/chat",
            json={
                "message": prompt,
                "history": [],
                "scope_mode": "selected_sources",
                "source_ids": [fixture["source_a_id"]],
            },
        )
        response.raise_for_status()
        chat_data = response.json()
        content = chat_data.get("content") or ""
        reference_time = str(chat_data.get("reference_time") or "")
        citations = chat_data.get("citations") or []
        scope = chat_data.get("scope") or {}

        assertions.append(
            _assert(
                "chat_api_scope_selected",
                scope.get("mode") == "selected_sources"
                and scope.get("source_ids") == [fixture["source_a_id"]]
                and scope.get("source_scope", {}).get("matched_source_ids") == [fixture["source_a_id"]],
                {"scope": scope},
            )
        )
        assertions.append(
            _assert(
                "chat_used_selected_claim_only",
                chat_data.get("claims_used") == 1
                and len(citations) == 1
                and citations[0].get("claim_id") == fixture["claim_a_id"],
                {"claims_used": chat_data.get("claims_used"), "citations": citations},
            )
        )
        assertions.append(
            _assert(
                "chat_response_non_empty_real_model",
                len(content.strip()) >= 30 and chat_data.get("model_used") not in ("mock", "error", None),
                {"model_used": chat_data.get("model_used"), "content_preview": content[:300]},
            )
        )
        assertions.append(
            _assert(
                "chat_response_grounding_terms",
                any(term in content.lower() for term in ["scope", "检索", "source", "证据", "透明", "ground"]),
                {"content_preview": content[:500]},
            )
        )
        assertions.append(
            _assert(
                "chat_reference_time_visible_in_content",
                bool(reference_time) and reference_time in content,
                {"reference_time": reference_time, "content_preview": content[:500]},
            )
        )

        status = "passed" if all(a["status"] == "passed" for a in assertions) else "failed"
        report = {
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "fixture": fixture,
            "ping": ping,
            "assertions": assertions,
            "chat": {
                "id": chat_data.get("id"),
                "claims_used": chat_data.get("claims_used"),
                "model_used": chat_data.get("model_used"),
                "search_tier": chat_data.get("search_tier"),
                "reference_time": chat_data.get("reference_time"),
                "scope": chat_data.get("scope"),
                "citations": chat_data.get("citations"),
                "content_preview": (chat_data.get("content") or "")[:1000],
            },
            "notes": [
                "This run uses a real OpenRouter call.",
                "It validates scoped real chat plumbing and minimal grounding, not broad financial accuracy.",
                "Created fixture records are preserved for audit and are not deleted.",
            ],
        }
        if write_report:
            root = Path(__file__).resolve().parents[4]
            report_dir = root / "docs" / "verification"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"real_scope_chat_acceptance_{fixture['run_id']}.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            report["report_path"] = str(report_path)
        return report
    finally:
        db.close()
        semantic_search.embed_text = old_embed


if __name__ == "__main__":
    try:
        result = run_acceptance(write_report=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["status"] == "passed" else 1)
    except Exception as exc:
        report = {
            "status": "failed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        sys.exit(2)
