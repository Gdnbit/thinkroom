from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ulid import ULID

from app.database import safe_commit
from app.schemas.claim_card import ClaimCard, ClaimEvidenceLink, ClaimRawSourceLink
from app.schemas.evidence_span import EvidenceSpan
from app.schemas.raw_source import RawSource


def _new_id() -> str:
    return str(ULID())


def test_selected_scope_sufficiency_refuses_structured_claim_not_yet_valid():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    now = datetime.now(timezone.utc)
    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [
                {
                    "title": "future-effective copper claim",
                    "summary": "Copper is bullish for next week, but the claim is not yet effective.",
                    "raw_text_preview": "As of 2026-06-20, copper is bullish for next week.",
                    "published_at": now.isoformat(),
                    "evidence": {
                        "content": "As of 2026-06-20, copper is bullish for next week.",
                    },
                }
            ],
        },
        citations=[
            SimpleNamespace(
                time_horizon="short",
                valid_from=now + timedelta(days=7),
                valid_until=None,
            )
        ],
    )

    reason = selected_scope_insufficiency_reason(
        "请只根据选定资料判断当前下周铜价会不会上涨，并给出交易建议。",
        ctx,
    )

    assert reason == "selected_sources_claim_validity_window_mismatch"


def test_selected_scope_sufficiency_refuses_geographic_scope_mismatch():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [{"title": "Europe Delta report", "evidence": {"content": "Europe demand fell 7%."}}],
        },
        citations=[
            SimpleNamespace(
                geographic_scope=["EU"],
                trust_weight=0.9,
                time_horizon="short",
                valid_from=None,
                valid_until=None,
            )
        ],
    )

    reason = selected_scope_insufficiency_reason(
        "请根据这份资料判断中国区项目 Delta 的需求变化。",
        ctx,
    )

    assert reason == "selected_sources_geographic_scope_mismatch"


def test_selected_scope_sufficiency_refuses_low_trust_actionable_chat_claim():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [{"title": "informal chat", "evidence": {"content": "Someone casually suggested full allocation."}}],
        },
        citations=[
            SimpleNamespace(
                geographic_scope=[],
                trust_weight=0.2,
                time_horizon="unspecified",
                valid_from=None,
                valid_until=None,
            )
        ],
    )

    reason = selected_scope_insufficiency_reason(
        "能否把这段聊天作为项目 Epsilon 的正式配置策略？",
        ctx,
    )

    assert reason == "selected_sources_low_trust_evidence"


def test_geographic_scope_parser_does_not_treat_status_as_us_scope():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [{"title": "EU status report", "evidence": {"content": "Current status is stable."}}],
        },
        citations=[
            SimpleNamespace(
                geographic_scope=["EU"],
                trust_weight=0.9,
                time_horizon="short",
                valid_from=None,
                valid_until=None,
            )
        ],
    )

    assert selected_scope_insufficiency_reason("Explain the current system status.", ctx) is None


def test_selected_scope_insufficiency_detail_explains_geography_and_low_trust():
    from app.api.v1.chat import _selected_scope_insufficiency_detail

    geography = _selected_scope_insufficiency_detail("selected_sources_geographic_scope_mismatch")
    low_trust = _selected_scope_insufficiency_detail("selected_sources_low_trust_evidence")

    assert "地域" in geography and "不匹配" in geography
    assert "低信任" in low_trust and "正式策略" in low_trust


def test_selected_scope_sufficiency_treats_explicit_absence_as_query_mismatch_with_citation():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [{
                "title": "unrelated weather record",
                "evidence": {
                    "content": "2026-06-30，办公地点天气晴朗。该资料不包含项目 Zeta 或预算信息。",
                },
            }],
        },
        citations=[
            SimpleNamespace(
                summary="该资料不包含项目 Zeta 或预算信息",
                geographic_scope=[],
                trust_weight=0.9,
                time_horizon="unspecified",
                valid_from=None,
                valid_until=None,
            )
        ],
    )

    assert selected_scope_insufficiency_reason(
        "截至 2026-06-30，项目 Zeta 的预算是否超支？",
        ctx,
    ) == "selected_sources_query_mismatch"


def test_selected_scope_sufficiency_ignores_entity_names_repeated_only_in_source_title():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [{
                "title": "Decision quality eval swq-insufficient-zeta source-zeta run-1",
                "article_summary": (
                    "2026-06-30，办公地点天气晴朗。"
                    "该资料不包含项目 Zeta 或预算信息。"
                ),
                "evidence": {
                    "content": (
                        "2026-06-30，办公地点天气晴朗。"
                        "该资料不包含项目 Zeta 或预算信息。"
                    ),
                },
            }],
        },
        citations=[
            SimpleNamespace(
                summary="该资料不包含项目 Zeta 或预算信息",
                geographic_scope=[],
                trust_weight=0.9,
                time_horizon="unspecified",
                valid_from=None,
                valid_until=None,
            )
        ],
    )

    assert selected_scope_insufficiency_reason(
        "截至 2026-06-30，项目 Zeta 的预算是否超支？",
        ctx,
    ) == "selected_sources_query_mismatch"


def test_selected_scope_sufficiency_accepts_chinese_multi_source_supply_demand_evidence():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [
                {
                    "title": "controlled source A",
                    "evidence": {
                        "content": "2026-06-30，中国区样本库存同比下降 8%。",
                    },
                },
                {
                    "title": "controlled source B",
                    "evidence": {
                        "content": "2026-06-30，中国区样本订单同比增长 12%。",
                    },
                },
            ],
        },
        citations=[],
    )

    assert selected_scope_insufficiency_reason(
        "截至 2026-06-30，综合库存和订单资料说明供需变化，并分别引用来源。",
        ctx,
    ) is None


def test_selected_scope_sufficiency_rejects_budget_question_with_only_weather_evidence():
    from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

    ctx = SimpleNamespace(
        source_scope={
            "mode": "selected_sources",
            "sources": [
                {
                    "title": "项目 Zeta 预算核对材料",
                    "evidence": {
                        "content": "2026-06-30，办公地点天气晴朗。",
                    },
                },
            ],
        },
        citations=[],
    )

    assert selected_scope_insufficiency_reason(
        "截至 2026-06-30，项目 Zeta 的预算是否超支？",
        ctx,
    ) == "selected_sources_query_mismatch"


def test_selected_source_chat_refuses_when_question_is_outside_selected_evidence(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="workflow reliability note",
        raw_text=(
            "Research OS main workflow should prioritize upload progress, "
            "source transparency, citation traceability, and conservative scoped answers."
        ),
        article_summary="Workflow reliability and source transparency note.",
        language="en",
        status="completed",
        word_count=16,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "insufficient evidence scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    answer = data["content"]
    scope = data["scope"]
    quality = data["quality_filters"]

    assert scope["mode"] == "selected_sources"
    assert scope["source_ids"] == [source.id]
    assert quality["evidence_sufficiency"] == "insufficient"
    assert quality["insufficient_evidence_reason"] == "selected_sources_query_mismatch"
    assert "选定资料" in answer
    assert "不足" in answer or "没有覆盖" in answer
    assert "铜价会上涨" not in answer
    assert data["claims_used"] == 0
    assert data["citations"] == []


def test_selected_source_chat_refuses_source_without_usable_text(client, db, monkeypatch):
    def fail_complete(**_kwargs):
        raise AssertionError("LLM should not be called when selected source has no usable evidence")

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fail_complete)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="pdf",
        title="pending upload without extracted text.pdf",
        raw_text=None,
        article_summary=None,
        language="zh",
        status="pending",
        word_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post(
        "/api/v1/chat/sessions",
        json={"title": "missing evidence scope", "mode": "chat"},
    )
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请总结这个文件。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] == "insufficient"
    assert quality["insufficient_evidence_reason"] == "selected_sources_no_usable_evidence"
    assert "尚未解析" in data["content"] or "处理失败" in data["content"]
    assert data["claims_used"] == 0
    assert data["citations"] == []
    assert data["llm_trace"]["model"] == "not_called_insufficient_evidence"
    assert data["route_identity"]["scope"] == "decision_chat_selected_source"
    assert data["route_identity"]["fingerprint"].startswith("sha256:")


def test_selected_source_chat_allows_bilingual_related_evidence(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="workflow reliability note",
        raw_text=(
            "Research OS upload progress, source transparency, citation traceability, "
            "and conservative scoped answers are required for a trustworthy workflow."
        ),
        article_summary="Workflow reliability, upload progress, and citation transparency note.",
        language="en",
        status="completed",
        word_count=15,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "bilingual related scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请根据选定资料说明上传进度和引用透明度为什么重要。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] != "insufficient"
    assert quality.get("insufficient_evidence_reason") is None
    assert data["model_used"] != "not_called_insufficient_evidence"
    assert data["citations"], "related selected source should remain available as a citation fallback"
    assert data["route_identity"]["scope"] == "decision_chat_selected_source"
    assert data["route_identity"]["fingerprint"].startswith("sha256:")


def test_selected_source_chat_refuses_actionable_advice_without_directional_evidence(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="copper supply-chain background note",
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
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "actionable insufficient scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    answer = data["content"]
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] == "insufficient"
    assert (
        quality["insufficient_evidence_reason"]
        == "selected_sources_actionable_question_requires_directional_evidence"
    )
    assert "选定资料" in answer
    assert "交易建议" in answer
    assert "铜价会上涨" not in answer
    assert data["claims_used"] == 0
    assert data["citations"] == []
    assert data["model_used"] == "not_called_insufficient_evidence"


def test_chat_generation_uses_fusion_generation_settings(client, monkeypatch):
    """普通问答的模型生成参数必须来自配置，避免硬编码导致主模型截断或高温漂移。"""
    from app.config import settings

    captured_calls: list[dict] = []

    monkeypatch.setattr(settings, "FUSION_MODEL", "test/fusion-model")
    monkeypatch.setattr(settings, "FUSION_FALLBACK_MODEL", "test/fallback-model")
    monkeypatch.setattr(settings, "FUSION_MAX_TOKENS", 1777)
    monkeypatch.setattr(settings, "FUSION_TEMPERATURE", 0.11)

    def fake_build_chat_context(**_kwargs):
        citation = SimpleNamespace(
            index=1,
            claim_id="claim-config-1",
            stance="support",
            summary="系统上传后应展示接收、解析、切片、向量化、入库五个阶段。",
            short_quote="系统上传后应展示接收、解析、切片、向量化、入库五个阶段。",
            trust_weight=0.9,
            confidence=0.9,
            time_horizon=None,
            speaker_id=None,
            valid_from=None,
            valid_until=None,
            source_id=None,
            evidence_span_id=None,
            span_type="raw_text",
            char_start=0,
            char_end=30,
            page_or_timestamp=None,
            extraction_method="test",
        )
        return SimpleNamespace(
            context_block="证据 [1]：系统上传后应展示接收、解析、切片、向量化、入库五个阶段。",
            citations=[citation],
            source_scope={"mode": "global", "sources": []},
            quality_filters={},
            stance_distribution={},
            balanced_applied=False,
            search_tier="test",
            total_candidates_scanned=1,
            market_snapshot=None,
        )

    def fake_complete(**kwargs):
        captured_calls.append(kwargs)
        return "截至 2026年07月05日 17:40:00 CST，系统上传后应展示接收、解析、切片、向量化、入库五个阶段 [1]。"

    def fake_usage():
        last = captured_calls[-1] if captured_calls else {}
        return {
            "model": last.get("model"),
            "requested_model": last.get("model"),
            "configured_fallback_models": ["test/fallback-model"],
            "skill_role": last.get("skill_role"),
            "duration_ms": 12,
            "prompt_hash": "sha256:test",
            "response_hash": "sha256:test",
            "fallback_chain": [last.get("model")] if last.get("model") else [],
            "response_chars": 64,
            "input_tokens": 100,
            "output_tokens": 32,
            "cost_usd": 0.0,
            "mock": False,
            "error": None,
            "error_type": None,
        }

    monkeypatch.setattr("app.services.chat_harness.build_chat_context", fake_build_chat_context)
    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    monkeypatch.setattr("app.utils.llm_client.llm_client.get_last_usage", fake_usage)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "generation settings", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={"message": "上传后有哪些阶段？", "history": []},
    )

    assert chat_resp.status_code == 200
    assert captured_calls
    first_call = captured_calls[0]
    assert first_call["model"] == "test/fusion-model"
    assert first_call["skill_role"] == "fusion"
    assert first_call["max_tokens"] == 1777
    assert first_call["temperature"] == 0.11
    trace = chat_resp.json()["llm_trace"]
    generation_trace = next(call for call in trace["calls"] if call["step"] == "chat_generation")
    assert generation_trace["requested_model"] == "test/fusion-model"
    assert generation_trace["configured_fallback_models"] == ["test/fallback-model"]


def test_chat_generation_uses_fusion_specific_fallback_model(client, monkeypatch):
    """普通问答 fallback 应优先使用 FUSION_FALLBACK_MODEL，而不是区域兜底模型。"""
    from app.config import settings

    captured_calls: list[dict] = []

    monkeypatch.setattr(settings, "FUSION_MODEL", "test/fusion-primary")
    monkeypatch.setattr(settings, "FUSION_FALLBACK_MODEL", "test/fusion-fallback")
    monkeypatch.setattr(settings, "REGION_FALLBACK_MODEL", "test/region-fallback")
    monkeypatch.setattr(settings, "FUSION_MAX_TOKENS", 1800)
    monkeypatch.setattr(settings, "FUSION_TEMPERATURE", 0.15)

    def fake_build_chat_context(**_kwargs):
        citation = SimpleNamespace(
            index=1,
            claim_id="claim-fallback-1",
            stance="support",
            summary="系统上传后应展示接收、解析、切片、向量化、入库五个阶段。",
            short_quote="系统上传后应展示接收、解析、切片、向量化、入库五个阶段。",
            trust_weight=0.9,
            confidence=0.9,
            time_horizon=None,
            speaker_id=None,
            valid_from=None,
            valid_until=None,
            source_id=None,
            evidence_span_id=None,
            span_type="raw_text",
            char_start=0,
            char_end=30,
            page_or_timestamp=None,
            extraction_method="test",
        )
        return SimpleNamespace(
            context_block="证据 [1]：系统上传后应展示接收、解析、切片、向量化、入库五个阶段。",
            citations=[citation],
            source_scope={"mode": "global", "sources": []},
            quality_filters={},
            stance_distribution={},
            balanced_applied=False,
            search_tier="test",
            total_candidates_scanned=1,
            market_snapshot=None,
        )

    def fake_complete(**kwargs):
        captured_calls.append(kwargs)
        if kwargs["model"] == "test/fusion-primary":
            return "短"
        return (
            "截至 2026年07月05日 17:40:00 CST，系统上传后应展示接收、解析、切片、向量化、入库五个阶段 [1]。"
            "这是针对 fallback 选择的回归测试回答，确保内容长度足够通过完整性检查 [1]。"
        )

    def fake_usage():
        last = captured_calls[-1] if captured_calls else {}
        return {
            "model": last.get("model"),
            "skill_role": last.get("skill_role"),
            "duration_ms": 12,
            "prompt_hash": "sha256:test",
            "response_hash": "sha256:test",
            "fallback_chain": [last.get("model")] if last.get("model") else [],
            "response_chars": 120,
            "input_tokens": 100,
            "output_tokens": 32,
            "cost_usd": 0.0,
            "mock": False,
            "error": None,
            "error_type": None,
        }

    monkeypatch.setattr("app.services.chat_harness.build_chat_context", fake_build_chat_context)
    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    monkeypatch.setattr("app.utils.llm_client.llm_client.get_last_usage", fake_usage)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "fallback settings", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={"message": "上传后有哪些阶段？", "history": []},
    )

    assert chat_resp.status_code == 200
    assert [call["model"] for call in captured_calls[:2]] == [
        "test/fusion-primary",
        "test/fusion-fallback",
    ]


def test_chat_response_prunes_extra_low_overlap_sentence_citations(client, monkeypatch):
    captured_calls: list[dict] = []

    def fake_build_chat_context(**_kwargs):
        citations = [
            SimpleNamespace(
                index=1,
                claim_id="claim-marker",
                stance="support",
                summary="回答必须原样写出验收标记",
                short_quote="Research OS E2E 验收标记:RO_E2E_123",
                trust_weight=0.9,
                confidence=0.9,
                time_horizon=None,
                speaker_id=None,
                valid_from=None,
                valid_until=None,
                source_id="source-prune",
                evidence_span_id="span-marker",
                span_type="quote",
                char_start=0,
                char_end=32,
                page_or_timestamp=None,
                extraction_method="test",
            ),
            SimpleNamespace(
                index=2,
                claim_id="claim-stages",
                stance="support",
                summary="系统上传后展示五个处理阶段",
                short_quote="系统应该展示五个可见阶段:接收、解析、切片、向量化、入库。",
                trust_weight=0.9,
                confidence=0.9,
                time_horizon=None,
                speaker_id=None,
                valid_from=None,
                valid_until=None,
                source_id="source-prune",
                evidence_span_id="span-stages",
                span_type="quote",
                char_start=33,
                char_end=82,
                page_or_timestamp=None,
                extraction_method="test",
            ),
        ]
        return SimpleNamespace(
            context_block=(
                "[1] Research OS E2E 验收标记:RO_E2E_123\n"
                "[2] 系统应该展示五个可见阶段:接收、解析、切片、向量化、入库。"
            ),
            citations=citations,
            source_scope={
                "mode": "selected_sources",
                "requested_source_ids": ["source-prune"],
                "matched_source_ids": ["source-prune"],
                "missing_source_ids": [],
                "sources": [{
                    "source_id": "source-prune",
                    "title": "citation-prune-source.txt",
                    "summary": "验收标记与上传阶段说明",
                    "raw_text_preview": (
                        "Research OS E2E 验收标记:RO_E2E_123。"
                        "系统应该展示五个可见阶段:接收、解析、切片、向量化、入库。"
                    ),
                    "evidence": {
                        "content": (
                            "Research OS E2E 验收标记:RO_E2E_123。"
                            "系统应该展示五个可见阶段:接收、解析、切片、向量化、入库。"
                        ),
                    },
                }],
            },
            quality_filters={"exclude_expired": True, "exclude_contradicted": True},
            stance_distribution={},
            balanced_applied=False,
            search_tier="test",
            total_candidates_scanned=2,
            market_snapshot=None,
        )

    def fake_complete(**kwargs):
        captured_calls.append(kwargs)
        return (
            "截至 2026年07月05日 18:00:00 CST，本次验收标记为 RO_E2E_123 [1][2]。\n"
            "系统上传后需要展示五个阶段：接收、解析、切片、向量化、入库 [1][2]。"
        )

    def fake_usage():
        last = captured_calls[-1] if captured_calls else {}
        return {
            "model": last.get("model"),
            "skill_role": last.get("skill_role"),
            "duration_ms": 12,
            "prompt_hash": "sha256:test",
            "response_hash": "sha256:test",
            "fallback_chain": [last.get("model")] if last.get("model") else [],
            "response_chars": 120,
            "input_tokens": 100,
            "output_tokens": 32,
            "cost_usd": 0.0,
            "mock": False,
            "error": None,
            "error_type": None,
        }

    monkeypatch.setattr("app.services.chat_harness.build_chat_context", fake_build_chat_context)
    monkeypatch.setattr("app.utils.llm_client.llm_client.complete", fake_complete)
    monkeypatch.setattr("app.utils.llm_client.llm_client.get_last_usage", fake_usage)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "citation prune", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "本次验收标记是什么？上传后有哪些阶段？",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": ["source-prune"],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    assert "[1][2]" not in data["content"]
    assert "RO_E2E_123 [1]" in data["content"]
    assert "入库 [2]" in data["content"]
    assert data["llm_trace"]["integrity_fallback_applied"] is False
    assert data["quality_report"]["evidence"]["low_overlap_citations"] == []


def test_selected_source_chat_refuses_time_sensitive_advice_without_time_window_evidence(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="copper directional note without horizon",
        raw_text=(
            "Copper demand is improving and analysts are bullish on copper. "
            "The note recommends holding long exposure because warehouse stocks are tight."
        ),
        article_summary="Bullish copper note without an explicit date or forecast horizon.",
        language="en",
        status="completed",
        word_count=19,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "temporal insufficient scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    answer = data["content"]
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] == "insufficient"
    assert (
        quality["insufficient_evidence_reason"]
        == "selected_sources_temporal_question_requires_time_window_evidence"
    )
    assert "选定资料" in answer
    assert "时间" in answer or "下周" in answer
    assert "铜价会上涨" not in answer
    assert data["claims_used"] == 0
    assert data["citations"] == []
    assert data["model_used"] == "not_called_insufficient_evidence"


def test_selected_source_chat_allows_actionable_advice_with_directional_time_window_evidence(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="copper next-week directional note",
        raw_text=(
            f"As of {now:%Y-%m-%d}, copper demand is improving and analysts are bullish on copper for next week. "
            "The note recommends holding long exposure because warehouse stocks are tight."
        ),
        article_summary="Next-week bullish copper note with explicit date and horizon.",
        language="en",
        status="completed",
        word_count=24,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "temporal sufficient scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] != "insufficient"
    assert quality.get("insufficient_evidence_reason") is None
    assert data["model_used"] != "not_called_insufficient_evidence"
    assert data["citations"], "time-window evidence should remain available as source citation fallback"


def test_selected_source_chat_allows_actionable_advice_with_published_at_time_anchor(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="dated copper directional note",
        raw_text=(
            "Copper demand is improving and analysts are bullish on copper. "
            "The note recommends holding long exposure because warehouse stocks are tight."
        ),
        article_summary="Bullish copper note with source publication metadata.",
        language="en",
        status="completed",
        word_count=19,
        published_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "published-at sufficient scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    quality = data["quality_filters"]
    scoped_source = data["scope"]["source_scope"]["sources"][0]

    assert scoped_source["published_at"].startswith(now.strftime("%Y-%m-%d"))
    assert quality["evidence_sufficiency"] != "insufficient"
    assert quality.get("insufficient_evidence_reason") is None
    assert data["model_used"] != "not_called_insufficient_evidence"
    assert data["citations"], "published_at should provide a usable selected-source time anchor"


def test_selected_source_chat_refuses_current_advice_when_time_anchor_is_stale(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="stale copper directional note",
        raw_text=(
            "Copper demand is improving and analysts are bullish on copper for next week. "
            "The note recommends holding long exposure because warehouse stocks are tight."
        ),
        article_summary="Stale bullish copper note with explicit horizon.",
        language="en",
        status="completed",
        word_count=23,
        published_at=datetime(2023, 1, 15, tzinfo=timezone.utc),
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "stale temporal scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断当前下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    answer = data["content"]
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] == "insufficient"
    assert (
        quality["insufficient_evidence_reason"]
        == "selected_sources_temporal_evidence_is_stale_for_current_question"
    )
    assert "选定资料" in answer
    assert "过时" in answer or "陈旧" in answer or "时效" in answer
    assert "铜价会上涨" not in answer
    assert data["claims_used"] == 0
    assert data["citations"] == []
    assert data["model_used"] == "not_called_insufficient_evidence"


def test_selected_source_chat_refuses_when_requested_horizon_conflicts_with_evidence_horizon(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="copper next-quarter directional note",
        raw_text=(
            f"As of {now:%Y-%m-%d}, copper demand is improving and analysts are bullish on copper for next quarter. "
            "The note recommends holding long exposure over the next quarter because warehouse stocks are tight."
        ),
        article_summary="Next-quarter bullish copper note with explicit date and horizon.",
        language="en",
        status="completed",
        word_count=29,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "horizon mismatch scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    answer = data["content"]
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] == "insufficient"
    assert (
        quality["insufficient_evidence_reason"]
        == "selected_sources_temporal_horizon_mismatch"
    )
    assert "选定资料" in answer
    assert "下周" in answer or "时间跨度" in answer or "周期" in answer
    assert "铜价会上涨" not in answer
    assert data["claims_used"] == 0
    assert data["citations"] == []
    assert data["model_used"] == "not_called_insufficient_evidence"


def test_selected_source_chat_refuses_when_structured_claim_horizon_conflicts_with_question(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=_new_id(),
        source_type="note",
        title="structured next-quarter copper source",
        raw_text=(
            "As of 2026-06-20, copper demand is improving and analysts are bullish on copper for next quarter. "
            "The note recommends holding long exposure over the next quarter."
        ),
        article_summary="Next-quarter bullish copper source.",
        language="en",
        status="completed",
        word_count=24,
        created_at=now,
        updated_at=now,
    )
    claim = ClaimCard(
        id=_new_id(),
        summary="Copper demand is improving and analysts are bullish on copper for next quarter",
        full_quote="Copper demand is improving and analysts are bullish on copper for next quarter.",
        claim_type="asset_view",
        stance="bullish",
        time_horizon="medium",
        asset_scope=["copper"],
        theme_scope=["industrial metals"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=["warehouse stocks are tight"],
        confidence=0.86,
        source_trust_weight=0.72,
        is_contradicted=False,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )
    span = EvidenceSpan(
        id=_new_id(),
        source_id=source.id,
        span_type="quote",
        content="As of 2026-06-20, copper demand is improving and analysts are bullish on copper for next quarter.",
        char_start=0,
        char_end=96,
        confidence=0.96,
        extraction_method="test_fixture",
        metadata_={"claim_id": claim.id},
        created_at=now,
        updated_at=now,
    )
    db.add_all([source, claim, span])
    db.flush()
    db.add_all([
        ClaimRawSourceLink(
            claim_id=claim.id,
            source_id=source.id,
            link_type="extracted_from",
            created_at=now,
            updated_at=now,
        ),
        ClaimEvidenceLink(
            claim_id=claim.id,
            evidence_span_id=span.id,
            relevance_score=0.98,
            created_at=now,
            updated_at=now,
        ),
    ])
    safe_commit(db)

    session_resp = client.post("/api/v1/chat/sessions", json={"title": "claim horizon mismatch scope", "mode": "chat"})
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    chat_resp = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": "请只根据选定资料判断下周铜价会不会上涨，并给出交易建议。",
            "history": [],
            "scope_mode": "selected_sources",
            "source_ids": [source.id],
        },
    )

    assert chat_resp.status_code == 200
    data = chat_resp.json()
    answer = data["content"]
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] == "insufficient"
    assert (
        quality["insufficient_evidence_reason"]
        == "selected_sources_claim_temporal_horizon_mismatch"
    )
    assert "选定资料" in answer
    assert "结构化观点" in answer or "时间跨度" in answer or "周期" in answer
    assert "铜价会上涨" not in answer
    assert data["claims_used"] == 0
    assert data["citations"] == []
    assert data["model_used"] == "not_called_insufficient_evidence"
