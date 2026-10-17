from __future__ import annotations

from datetime import datetime, timezone

from ulid import ULID

from app.schemas.claim_card import ClaimCard, ClaimEvidenceLink, ClaimRawSourceLink
from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink
from app.schemas.evidence_span import EvidenceSpan
from app.schemas.raw_source import RawSource


def _add_trace_source_claim(db, *, text: str = "MVP 演示必须保证回答可引用、可追溯，并清楚展示资料来源。") -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    source_id = str(ULID())
    claim_id = str(ULID())
    span_id = str(ULID())
    db.add(
        RawSource(
            id=source_id,
            source_type="note",
            title="trace-source.txt",
            raw_text=text,
            article_summary="MVP 演示主流程要求",
            language="zh",
            status="completed",
            word_count=30,
            created_at=now,
            updated_at=now,
        )
    )
    claim = ClaimCard(
        id=claim_id,
        summary=text[:80],
        full_quote=text,
        claim_type="risk_warning",
        stance="neutral",
        time_horizon="short",
        asset_scope=[],
        theme_scope=["mvp"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=["trace test"],
        confidence=0.9,
        source_trust_weight=0.8,
        is_contradicted=False,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(claim)
    db.flush()
    db.add(
        EvidenceSpan(
            id=span_id,
            source_id=source_id,
            span_type="quote",
            content=text,
            char_start=0,
            char_end=len(text),
            confidence=0.96,
            extraction_method="test_exact_quote",
            metadata_={"claim_id": claim_id},
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        ClaimRawSourceLink(
            claim_id=claim_id,
            source_id=source_id,
            link_type="extracted_from",
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        ClaimEvidenceLink(
            claim_id=claim_id,
            evidence_span_id=span_id,
            relevance_score=0.96,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return source_id, claim_id


def test_chat_endpoint_persists_decision_trace_events(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    source_id, _claim_id = _add_trace_source_claim(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"mode": "chat", "asset": "GENERAL"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )
    assert answer_resp.status_code == 200
    answer_data = answer_resp.json()
    assistant_id = answer_data["id"]
    timing_trace = answer_data["timing_trace"]
    assert timing_trace["total_ms"] >= 0
    assert timing_trace["retrieval_ms"] >= 0
    assert timing_trace["model_calls_ms"] >= 0

    from app.schemas.chat import ChatMessage

    persisted_message = db.get(ChatMessage, assistant_id)
    assert persisted_message is not None
    assert persisted_message.payload["timing_trace"] == timing_trace

    rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "chat_message",
            DecisionTraceEvent.related_object_id == assistant_id,
        )
        .all()
    )

    event_types = {row.event_type for row in rows}
    assert {"scope", "retrieval", "quality", "guardrail", "llm_call"}.issubset(event_types)
    assert all(row.session_id == session_id for row in rows)
    assert all(row.source_ids == [source_id] for row in rows)
    llm_rows = [row for row in rows if row.event_type == "llm_call"]
    assert llm_rows
    assert llm_rows[0].model
    retrieval_row = [row for row in rows if row.event_type == "retrieval"][0]
    assert retrieval_row.payload["citations_count"] >= 1
    assert retrieval_row.payload["candidates_scanned"] >= 1
    assert retrieval_row.payload["scope_mode"] == "selected_sources"


def test_chat_query_rewrite_llm_call_is_in_timing_cost_and_trace(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)
    source_id, _claim_id = _add_trace_source_claim(db)
    session_id = client.post("/api/v1/chat/sessions", json={"mode": "chat"}).json()["id"]

    response = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP?",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert response.status_code == 200
    data = response.json()
    calls = data["llm_trace"]["calls"]
    rewrite_call = next(call for call in calls if call.get("step") == "query_rewrite")
    assert rewrite_call["model"]
    assert data["llm_trace"]["call_count"] == len(calls)
    assert data["timing_trace"]["model_calls_ms"] >= int(rewrite_call.get("duration_ms") or 0)

    rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "chat_message",
            DecisionTraceEvent.related_object_id == data["id"],
            DecisionTraceEvent.event_type == "llm_call",
        )
        .all()
    )
    assert any(row.step_name == "query_rewrite" for row in rows)


def test_global_chat_trace_links_actual_citation_source(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    source_id, claim_id = _add_trace_source_claim(db)

    from app.schemas.claim_card import ClaimCard as _ClaimCard

    def fake_semantic_search(**_kwargs):
        return db.query(_ClaimCard).filter(_ClaimCard.id == claim_id).all()

    monkeypatch.setattr("app.services.chat_harness.sem_search.semantic_search", fake_semantic_search)

    session_resp = client.post("/api/v1/chat/sessions", json={"mode": "chat", "asset": "GENERAL"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "global",
        },
    )
    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assistant_id = data["id"]

    assert data["scope"]["mode"] == "global"
    assert data["citations"]
    assert data["citations"][0]["source_id"] == source_id

    rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "chat_message",
            DecisionTraceEvent.related_object_id == assistant_id,
        )
        .all()
    )
    assert rows
    assert all(source_id in (row.source_ids or []) for row in rows)
    assert (
        db.query(DecisionTraceSourceLink)
        .filter(DecisionTraceSourceLink.source_id == source_id)
        .count()
        >= len(rows)
    )


def test_chat_endpoint_reports_actual_trace_model(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")
    private_provider_error = "provider credential fragment must not reach public chat trace"

    from app.utils.llm_client import llm_client

    def fake_complete(**_kwargs):
        llm_client._last_usage = {
            "model": "actual/fallback-model",
            "skill_role": "fusion",
            "duration_ms": 321,
            "input_tokens": 100,
            "output_tokens": 60,
            "cost_usd": 0.001,
            "cost_source": "provider_usage",
            "generation_id": "gen-fallback-model",
            "fallback_chain": ["configured/primary-model", "actual/fallback-model"],
            "error": private_provider_error,
            "error_type": "rate_limited",
        }
        return (
            "截至 2026-06-22 03:45 CST，MVP 演示需要可追溯，因为用户必须看到资料来源、"
            "引用证据和模型调用记录才能确认回答并非凭空生成 [1]。这条回答专门用于验证模型"
            "展示口径应与真实 trace 保持一致，而不是显示配置模型。"
        )

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"mode": "chat", "asset": "GENERAL"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert data["llm_trace"]["model"] == "actual/fallback-model"
    assert data["llm_trace"]["error_type"] == "rate_limited"
    assert data["llm_trace"]["cost_source"] == "provider_usage"
    assert data["llm_trace"]["generation_id"] == "gen-fallback-model"
    assert data["llm_trace"]["calls"][0]["cost_source"] == "provider_usage"
    assert data["llm_trace"]["calls"][0]["generation_id"] == "gen-fallback-model"
    assert data["model_used"] == data["llm_trace"]["model"]
    assert "error" not in data["llm_trace"]
    assert all("error" not in call for call in data["llm_trace"]["calls"])
    assert private_provider_error not in answer_resp.text

    history_resp = client.get(f"/api/v1/chat/sessions/{session_id}/messages")
    assert history_resp.status_code == 200
    assert private_provider_error not in history_resp.text

    trace_rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "chat_message",
            DecisionTraceEvent.related_object_id == data["id"],
            DecisionTraceEvent.event_type == "llm_call",
        )
        .all()
    )
    assert trace_rows
    assert all("error" not in (row.payload or {}) for row in trace_rows)


def test_chat_generation_failure_returns_stable_public_error_without_exception_text(
    client, db, monkeypatch
):
    private_provider_error = "provider response with credential fragment must not leak"
    from app.utils.llm_client import llm_client

    def fail_complete(**_kwargs):
        llm_client._last_usage = {}
        raise RuntimeError(private_provider_error)

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fail_complete)
    source_id, _claim_id = _add_trace_source_claim(db)
    session_resp = client.post(
        "/api/v1/chat/sessions",
        json={"mode": "chat", "asset": "GENERAL"},
    )
    session_id = session_resp.json()["id"]

    response = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["llm_trace"]["error_type"] == "chat_generation_failed"
    assert data["llm_trace"]["calls"][0]["error_type"] == "chat_generation_failed"
    assert "error" not in data["llm_trace"]
    assert private_provider_error not in response.text
    assert "请稍后重试" in data["content"]

    history = client.get(f"/api/v1/chat/sessions/{session_id}/messages")
    assert history.status_code == 200
    assert private_provider_error not in history.text


def test_chat_endpoint_retries_output_integrity_failure_before_persisting(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")

    from app.utils.llm_client import llm_client

    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            llm_client._last_usage = {
                "model": "actual/primary-model",
                "skill_role": "fusion",
                "duration_ms": 210,
                "input_tokens": 110,
                "output_tokens": 70,
                "cost_usd": 0.001,
            }
            return (
                "截至 2026-06-22 03:45 CST，本次回答需要遵守 [输出约束]，"
                "This means the assistant must not mention system prompt or developer message, "
                "but the generated answer accidentally exposes internal instruction details. "
                "这段内容长度足够长，避免被短回答兜底误判，同时明确模拟提示词泄漏和异常英文混杂，"
                "因此不能直接作为用户可见的正式决策回答 [1]。"
            )
        llm_client._last_usage = {
            "model": "actual/retry-model",
            "skill_role": "fusion",
            "duration_ms": 330,
            "input_tokens": 130,
            "output_tokens": 90,
            "cost_usd": 0.002,
        }
        return (
            "截至 2026-06-22 03:45 CST，MVP 演示需要可追溯，因为用户必须确认资料来源、"
            "引用证据和模型调用记录都与本次回答绑定，才能判断回答不是凭空生成 [1]。"
            "在当前资料范围内，更稳妥的结论是先把回答作为研究参考，而不是直接当成交易指令。"
        )

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"mode": "chat", "asset": "GENERAL"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert len(calls) == 2
    assert "输出约束" not in data["content"]
    assert "system prompt" not in data["content"]
    assert "不是凭空生成" in data["content"]
    assert data["model_used"] == "actual/retry-model"
    assert data["llm_trace"]["call_count"] == 2
    assert data["llm_trace"]["total_cost_usd"] == 0.003
    assert data["total_cost_usd"] == 0.003
    assert data["llm_trace"]["integrity_retry_attempted"] is True
    assert data["llm_trace"]["integrity_retry_succeeded"] is True
    assert data["llm_trace"]["model"] == "actual/retry-model"

    assistant_id = data["id"]
    rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "chat_message",
            DecisionTraceEvent.related_object_id == assistant_id,
            DecisionTraceEvent.event_type == "llm_call",
        )
        .order_by(DecisionTraceEvent.payload["call_index"].as_integer())
        .all()
    )
    assert len(rows) == 2
    assert rows[0].payload["error_type"] == "output_integrity_failed"
    assert rows[1].model == "actual/retry-model"


def test_chat_endpoint_retries_when_available_citations_are_not_used(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")

    from app.utils.llm_client import llm_client

    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            llm_client._last_usage = {
                "model": "actual/primary-model",
                "skill_role": "fusion",
                "duration_ms": 240,
                "input_tokens": 100,
                "output_tokens": 80,
                "cost_usd": 0.001,
            }
            return (
                "截至 2026-06-22 03:45 CST，MVP 演示需要可追溯，因为用户必须确认资料来源、"
                "引用证据和模型调用记录都与本次回答绑定，才能判断回答不是凭空生成。"
                "这段回答故意没有写出任何引用编号，用于验证系统会自动重试生成带引用的正式回答。"
            )
        llm_client._last_usage = {
            "model": "actual/retry-model",
            "skill_role": "fusion",
            "duration_ms": 360,
            "input_tokens": 130,
            "output_tokens": 90,
            "cost_usd": 0.002,
        }
        return (
            "截至 2026-06-22 03:45 CST，MVP 演示需要可追溯，因为用户必须确认资料来源、"
            "引用证据和模型调用记录都与本次回答绑定，才能判断回答不是凭空生成 [1]。"
            "在当前资料范围内，更稳妥的结论是先把回答作为研究参考，而不是直接当成交易指令。"
        )

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"mode": "chat", "asset": "GENERAL"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert "[1]" in data["content"]
    assert len(calls) >= 2
    assert data["llm_trace"]["calls"][-1]["step"] == "integrity_retry"
    assert data["llm_trace"]["integrity_retry_attempted"] is True
    assert data["llm_trace"]["integrity_retry_succeeded"] is True
    assert data["llm_trace"]["integrity_retry_reasons"] == ["citations_not_used"]
    assert data["quality_report"]["decision_ready"] is True
    assert data["quality_report"]["evidence"]["used_citation_count"] == 1
    assert data["quality_report"]["evidence"]["unused_citation_count"] == 0
    assert "available_citations_not_used" not in {
        warning["code"] for warning in data["quality_report"]["warnings"]
    }


def test_chat_endpoint_retries_when_material_claim_is_uncited(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "configured/primary-model")

    from app.utils.llm_client import llm_client

    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            llm_client._last_usage = {
                "model": "actual/primary-model",
                "skill_role": "fusion",
                "duration_ms": 260,
                "input_tokens": 100,
                "output_tokens": 80,
                "cost_usd": 0.001,
            }
            return (
                "截至 2026-06-22 03:45 CST，原油库存上升会压制油价 [1]。"
                "这份资料还说明，库存压力会降低短期做多的确定性，"
                "因此回答需要把结论限定在当前选定资料范围内 [1]。"
                "然而，如果忽略这些限制，下周可以加仓原油多单。"
                "这最后一句故意不带引用，用于验证系统会自动重写关键操作判断。"
            )
        llm_client._last_usage = {
            "model": "actual/retry-model",
            "skill_role": "fusion",
            "duration_ms": 380,
            "input_tokens": 140,
            "output_tokens": 100,
            "cost_usd": 0.002,
        }
        return (
            "截至 2026-06-22 03:45 CST，原油库存上升会压制油价，"
            "缺少加仓多单依据，应保持观望 [1]。"
        )

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="原油库存上升会压制油价，缺少加仓多单依据，应保持观望。",
    )

    session_resp = client.post(
        "/api/v1/chat/sessions",
        json={"mode": "chat", "asset": "GENERAL"},
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断原油库存对油价方向的影响？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert len(calls) >= 2
    assert data["llm_trace"]["calls"][-1]["step"] == "integrity_retry"
    assert data["llm_trace"]["integrity_retry_attempted"] is True
    assert data["llm_trace"]["integrity_retry_succeeded"] is True
    assert data["llm_trace"]["integrity_retry_reasons"] == ["uncited_material_claim"]
    assert "加仓原油多单" not in data["content"]
    assert "应保持观望 [1]" in data["content"]
    assert data["quality_report"]["decision_ready"] is True
    assert "uncited_material_claim" not in {
        warning["code"] for warning in data["quality_report"]["warnings"]
    }


def test_chat_endpoint_uses_grounded_fallback_when_retry_still_has_citation_failures(
    client,
    db,
    monkeypatch,
):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "configured/primary-model")

    from app.utils.llm_client import llm_client

    bad_reply = (
        "截至 2026-06-22 03:45 CST，原油库存上升会压制油价，缺少加仓多单依据 [1]。"
        "该资料还要求回答严格限定在当前选定资料范围内，并保留引用以便用户复核 [1]。"
        "然而，下周可以加仓原油多单。这句没有证据支持，却被模型重复写入最终回答，"
        "用于验证第二次生成仍未通过引用完整性检查时，系统不会把它直接展示给用户。"
    )

    def fake_complete(**_kwargs):
        llm_client._last_usage = {
            "model": "actual/retry-model",
            "skill_role": "fusion",
            "duration_ms": 300,
            "input_tokens": 120,
            "output_tokens": 90,
            "cost_usd": 0.002,
        }
        return bad_reply

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="原油库存上升会压制油价，缺少加仓多单依据，应保持观望。",
    )
    session_resp = client.post(
        "/api/v1/chat/sessions",
        json={"mode": "chat", "asset": "GENERAL"},
    )
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断原油库存对油价方向的影响？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert (
        data["llm_trace"]["integrity_prune_succeeded"]
        or data["llm_trace"]["integrity_fallback_applied"]
    ), {"trace": data["llm_trace"], "content": data["content"]}
    assert "下周可以加仓原油多单" not in data["content"]
    assert "原油库存上升会压制油价" in data["content"]
    assert "证据不足以支持超出上述资料范围" in data["content"]
    assert data["llm_trace"]["integrity_retry_attempted"] is True
    assert data["llm_trace"]["integrity_retry_succeeded"] is False
    assert data["quality_report"]["decision_ready"] is True
    assert data["quality_report"]["severity"] == "ok"
    assert data["decision_guardrail"]["status"] == "passed"


def test_chat_endpoint_retries_when_citation_direction_conflicts(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "configured/primary-model")

    from app.utils.llm_client import llm_client

    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            llm_client._last_usage = {
                "model": "actual/primary-model",
                "skill_role": "fusion",
                "duration_ms": 260,
                "input_tokens": 100,
                "output_tokens": 80,
                "cost_usd": 0.001,
            }
            return (
                "截至 2026-06-22 03:45 CST，原油库存上升压制油价，但油价将上涨走强 [1]。"
                "这段文字只是为了提供足够长度，满足完整性检查的文本长度；"
                "后续内容不讨论市场方向，只描述测试上下文和展示格式。"
            )
        llm_client._last_usage = {
            "model": "actual/retry-model",
            "skill_role": "fusion",
            "duration_ms": 380,
            "input_tokens": 140,
            "output_tokens": 100,
            "cost_usd": 0.002,
        }
        return (
            "截至 2026-06-22 03:45 CST，原油库存连续上升，油价承压下行 [1]。"
        )

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="EIA 原油库存连续上升，油价承压下行。",
    )

    session_resp = client.post(
        "/api/v1/chat/sessions",
        json={"mode": "chat", "asset": "GENERAL"},
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周原油是否可以加仓多单？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert len(calls) >= 2
    assert data["llm_trace"]["calls"][-1]["step"] == "integrity_retry"
    assert data["llm_trace"]["integrity_retry_attempted"] is True
    assert data["llm_trace"]["integrity_retry_succeeded"] is True
    assert data["llm_trace"]["integrity_retry_reasons"] == ["citation_direction_conflict"]
    assert "走强上涨" not in data["content"]
    assert "油价承压下行 [1]" in data["content"]
    assert data["quality_report"]["decision_ready"] is True
    assert "citation_direction_conflict" not in {
        warning["code"] for warning in data["quality_report"]["warnings"]
    }


def test_chat_endpoint_retries_when_citation_text_overlap_is_low(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "configured/primary-model")

    from app.utils.llm_client import llm_client

    calls: list[dict] = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            llm_client._last_usage = {
                "model": "actual/primary-model",
                "skill_role": "fusion",
                "duration_ms": 260,
                "input_tokens": 100,
                "output_tokens": 80,
                "cost_usd": 0.001,
            }
            return (
                "截至 2026-06-22 03:45 CST，BTC 技术面明显走强并突破阻力 [1]。"
                "这段文字只是为了提供足够长度，满足完整性检查的文本长度；"
                "后续内容不讨论新的市场方向，只描述测试上下文和展示格式。"
            )
        llm_client._last_usage = {
            "model": "actual/retry-model",
            "skill_role": "fusion",
            "duration_ms": 380,
            "input_tokens": 140,
            "output_tokens": 100,
            "cost_usd": 0.002,
        }
        return (
            "截至 2026-06-22 03:45 CST，MVP 演示需要可追溯，因为用户必须看到资料来源、"
            "引用证据和模型调用记录都与本次回答绑定，才能判断回答不是凭空生成 [1]。"
        )

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="MVP 演示必须保证回答可引用、可追溯，并清楚展示资料来源和模型调用记录。",
    )

    session_resp = client.post(
        "/api/v1/chat/sessions",
        json={"mode": "chat", "asset": "GENERAL"},
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert len(calls) >= 2
    assert "不要把无关市场、无关资产或无关主题挂到现有引用编号上" in calls[1]["system_prompt"]
    assert data["llm_trace"]["calls"][-1]["step"] == "integrity_retry"
    assert data["llm_trace"]["integrity_retry_attempted"] is True
    assert data["llm_trace"]["integrity_retry_succeeded"] is True
    assert data["llm_trace"]["integrity_retry_reasons"] == ["citation_low_text_overlap"]
    assert "BTC 技术面明显走强" not in data["content"]
    assert "不是凭空生成 [1]" in data["content"]
    assert data["quality_report"]["decision_ready"] is True
    assert "citation_low_text_overlap" not in {
        warning["code"] for warning in data["quality_report"]["warnings"]
    }


def test_chat_endpoint_marks_quality_report_target_when_guardrail_rewrites_final_answer(
    client,
    db,
    monkeypatch,
):
    monkeypatch.setattr("app.config.settings.FUSION_MODEL", "configured/primary-model")
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "configured/primary-model")

    from app.schemas.chat import ChatMessage
    from app.utils.llm_client import llm_client

    bad_reply = (
        "截至 2026-06-22 03:45 CST，[输出约束] This generated answer exposes hidden "
        "system instructions, developer messages, internal policies, retry rules, and "
        "private chain diagnostics in a long English fragment that should never be shown [1]。"
    )

    def fake_complete(**kwargs):
        llm_client._last_usage = {
            "model": "actual/primary-model",
            "skill_role": "fusion",
            "duration_ms": 260,
            "input_tokens": 100,
            "output_tokens": 80,
            "cost_usd": 0.001,
        }
        return bad_reply

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="MVP 演示必须保证回答可引用、可追溯，并清楚展示资料来源和模型调用记录。",
    )

    session_resp = client.post(
        "/api/v1/chat/sessions",
        json={"mode": "chat", "asset": "GENERAL"},
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    answer_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "MVP 演示为什么需要可追溯？",
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )

    assert answer_resp.status_code == 200
    data = answer_resp.json()
    assert data["content"].startswith("截至 2026年")
    assert "**输出质量提示**" in data["content"]
    assert "hidden system instructions" not in data["content"]
    assert data["decision_guardrail"]["status"] == "blocked_output"
    revision = data["quality_report"]["answer_revision"]
    assert revision["quality_report_target"] == "model_output_before_guardrail"
    assert revision["final_answer_modified_by_guardrail"] is True
    assert revision["guardrail_status"] == "blocked_output"
    assert revision["evaluated_answer_hash"] != revision["final_answer_hash"]

    persisted = db.query(ChatMessage).filter(ChatMessage.id == data["id"]).one()
    assert persisted.content == data["content"]
    assert persisted.payload["quality_report"]["answer_revision"] == revision


def test_briefing_endpoint_persists_retrieval_trace_event(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="Research OS 简报演示必须展示资料召回、证据引用、护栏和最终建议。",
    )

    resp = client.post(
        "/api/v1/briefings",
        json={
            "question": "Research OS 简报演示需要展示哪些可信环节？",
            "asset_scope": ["GENERAL"],
            "source_ids": [source_id],
            "committee_level": "single",
        },
    )

    assert resp.status_code == 200
    briefing_id = resp.json()["id"]
    rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "briefing",
            DecisionTraceEvent.related_object_id == briefing_id,
        )
        .all()
    )

    retrieval_rows = [row for row in rows if row.event_type == "retrieval"]
    assert retrieval_rows
    assert retrieval_rows[0].source_ids == [source_id]
    assert retrieval_rows[0].payload["claims_count"] >= 1
    assert retrieval_rows[0].payload["scope_mode"] == "selected_sources"


def test_briefing_quality_report_respects_guardrail_warn_only(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="Research OS 简报演示需要保留证据回执，但低置信结果只能作为研究线索。",
    )

    def warn_only_guardrail(**kwargs):
        return {
            "mode": kwargs.get("mode"),
            "status": "warn_only",
            "original_action": kwargs.get("recommended_action") or "watch",
            "effective_action": "watch",
            "actionable_action_detected": False,
            "decision_ready": False,
            "evidence_count": kwargs.get("evidence_count") or 0,
            "source_mode": kwargs.get("source_mode") or "live",
            "is_mock": kwargs.get("is_mock") or False,
            "confidence": kwargs.get("confidence"),
            "reasons": ["low_confidence"],
            "warning_messages": ["低置信结果需要人工复核"],
        }

    monkeypatch.setattr(
        "app.api.v1.briefings.build_structured_decision_guardrail",
        warn_only_guardrail,
    )

    resp = client.post(
        "/api/v1/briefings",
        json={
            "question": "Research OS 简报演示如何处理低置信结果？",
            "asset_scope": ["GENERAL"],
            "source_ids": [source_id],
            "committee_level": "single",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["decision_guardrail"]["status"] == "warn_only"
    assert data["decision_guardrail"]["decision_ready"] is False
    assert data["quality_report"]["decision_ready"] is False


def test_committee_endpoint_persists_retrieval_trace_event(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="Research OS 委员会演示需要基于同一批资料进行多角度审查和裁决。",
    )

    resp = client.post(
        "/api/v1/committee/run",
        json={
            "question": "委员会演示应该如何证明资料被使用？",
            "asset": "GENERAL",
            "num_rounds": 1,
            "source_ids": [source_id],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    debate_id = body["debate_id"]
    assert body["route_identity"]["scope"] == "committee_selected_source"
    assert body["llm_trace"]["calls"]
    assert all(not str(call.get("model") or "").startswith("committee_") for call in body["llm_trace"]["calls"])
    detail_response = client.get(f"/api/v1/committee/{debate_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    actual_round_models = {
        str(call["model"])
        for call in body["llm_trace"]["calls"]
        if str(call.get("step") or "").startswith("round_")
    }
    assert actual_round_models
    assert all(round_item["model_used"] in actual_round_models for round_item in detail["rounds"])
    assert all(not str(model).startswith("committee_") for model in detail["model_by_seat"].values())
    assert detail["adjudication"]["adjudicated_by"] == body["llm_trace"]["calls"][-1]["model"]
    assert detail["route_identity"]["fingerprint"] == body["route_identity"]["fingerprint"]
    assert body["data_sources"]["claims"]["count"] >= 1
    assert body["data_sources"]["claims"]["items"][0]["summary"]
    rows = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "debate",
            DecisionTraceEvent.related_object_id == debate_id,
        )
        .all()
    )

    retrieval_rows = [row for row in rows if row.event_type == "retrieval"]
    assert retrieval_rows
    assert retrieval_rows[0].source_ids == [source_id]
    assert retrieval_rows[0].payload["claims_count"] >= 1
    assert retrieval_rows[0].payload["scope_mode"] == "selected_sources"


def test_committee_quality_report_respects_guardrail_warn_only(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    source_id, _claim_id = _add_trace_source_claim(
        db,
        text="Research OS 委员会演示需要让低置信裁决进入复核，而不是标记为质量通过。",
    )

    def warn_only_guardrail(**kwargs):
        return {
            "mode": kwargs.get("mode"),
            "status": "warn_only",
            "original_action": kwargs.get("recommended_action") or "watch",
            "effective_action": "watch",
            "actionable_action_detected": False,
            "decision_ready": False,
            "evidence_count": kwargs.get("evidence_count") or 0,
            "source_mode": kwargs.get("source_mode") or "live",
            "is_mock": kwargs.get("is_mock") or False,
            "confidence": kwargs.get("confidence"),
            "reasons": ["low_confidence"],
            "warning_messages": ["低置信委员会裁决需要人工复核"],
        }

    monkeypatch.setattr(
        "app.workflows.committee_workflow.build_structured_decision_guardrail",
        warn_only_guardrail,
    )

    resp = client.post(
        "/api/v1/committee/run",
        json={
            "question": "委员会演示如何处理低置信裁决？",
            "asset": "GENERAL",
            "num_rounds": 1,
            "source_ids": [source_id],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["decision_guardrail"]["status"] == "warn_only"
    assert body["decision_guardrail"]["decision_ready"] is False
    assert body["quality_report"]["decision_ready"] is False


def test_committee_refuses_selected_source_without_actionable_evidence(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="copper background note",
        raw_text=(
            "Copper is an industrial metal used in power grids and construction. "
            "This note only describes basic supply-chain background and warehouse classification."
        ),
        article_summary="Copper supply-chain background and warehouse classification note.",
        language="en",
        status="completed",
        word_count=19,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()

    resp = client.post(
        "/api/v1/committee/run",
        json={
            "question": "请只根据选定资料判断下周铜价会不会上涨，并让委员会给出操作建议。",
            "asset": "GENERAL",
            "num_rounds": 1,
            "source_ids": [source.id],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    quality = body["quality_filters"]

    assert body["success"] is True
    assert quality["evidence_sufficiency"] == "insufficient"
    assert (
        quality["insufficient_evidence_reason"]
        == "selected_sources_actionable_question_requires_directional_evidence"
    )
    assert body["recommended_action"] == "watch"
    assert body["direction"] == "neutral"
    assert body["round_count"] == 0
    assert body["llm_trace"]["call_count"] == 0
    assert body["route_identity"]["scope"] == "committee_selected_source"
    assert body["quality_report"]["decision_ready"] is False
    assert body["quality_report"]["warnings"][0]["code"] == "insufficient_evidence"
    field_evidence = body["quality_report"]["evidence"]["field_evidence"]
    assert set(field_evidence) == {
        "direction",
        "recommended_action",
        "synthesis",
        "risk_warnings",
        "structure_warnings",
    }
    assert all(item["status"] == "insufficient" for item in field_evidence.values())
    assert field_evidence["recommended_action"]["source_ids"] == [source.id]
    assert body["data_sources"]["claims"]["count"] == 0

    from app.schemas.debate import DebateRound

    rounds = db.query(DebateRound).filter(DebateRound.debate_id == body["debate_id"]).all()
    assert rounds == []

    detail_resp = client.get(f"/api/v1/committee/{body['debate_id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["quality_filters"] == quality
    assert detail["decision_guardrail"]["effective_action"] == "watch"
    assert detail["llm_trace"]["call_count"] == 0
    assert detail["route_identity"]["fingerprint"] == body["route_identity"]["fingerprint"]
    assert detail["quality_report"]["decision_ready"] is False
    assert detail["quality_report"]["warnings"][0]["code"] == "insufficient_evidence"
    detail_field_evidence = detail["quality_report"]["evidence"]["field_evidence"]
    assert detail_field_evidence["recommended_action"]["status"] == "insufficient"
    assert detail_field_evidence["recommended_action"]["source_ids"] == [source.id]
    assert detail["scope"]["mode"] == "selected_sources"
    assert detail["scope"]["source_ids"] == [source.id]
    assert detail["claims_used"] == 0
    assert not str(detail["model_baseline"]).startswith("committee_")
