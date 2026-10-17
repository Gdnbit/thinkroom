from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from ulid import ULID

from app.api.v1.chat import (
    SessionCreate,
    _clean_source_ids,
    _job_ids_from_payload,
    _latest_session_job_ids,
    _latest_session_source_ids,
    _source_ids_from_payload,
)
from app.schemas.chat import ChatMessage, ChatSession
from app.schemas.claim_card import ClaimCard, ClaimEvidenceLink, ClaimRawSourceLink
from app.schemas.evidence_span import EvidenceSpan
from app.schemas.job_run import JobRun
from app.schemas.raw_source import RawSource
from app.services.chat_harness import _build_claim_block, build_chat_context


def test_clean_source_ids_deduplicates_and_preserves_order():
    assert _clean_source_ids([" A ", "", "B", "A", None, " C "]) == ["A", "B", "C"]


def test_new_chat_session_defaults_to_general_asset():
    assert SessionCreate().asset == "GENERAL"


def test_source_ids_from_payload_accepts_string_and_list_shapes():
    assert _source_ids_from_payload({
        "upload_receipt": {"source_ids": ["receipt-source"]},
        "scope": {"source_ids": "single-source"},
        "ingestion": {"sourceIds": ["batch-source"]},
        "source_ids": ["single-source", "top-source"],
    }) == ["receipt-source", "single-source", "batch-source", "top-source"]


def test_job_ids_from_payload_accepts_upload_receipt_and_ingestion_shapes():
    assert _job_ids_from_payload({
        "upload_receipt": {"job_ids": ["job-a", "job-b"]},
        "ingestion": {"jobIds": ["job-b", "job-c"]},
        "job_ids": "job-d",
    }) == ["job-a", "job-b", "job-c", "job-d"]


def test_latest_session_source_ids_reads_recent_message_payload(db):
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="scope-test",
        mode="chat",
        asset="BTC",
        created_at=now,
        updated_at=now,
    )
    old_msg = ChatMessage(
        id=str(ULID()),
        session_id=session.id,
        role="assistant",
        content="old",
        payload={"scope": {"source_ids": ["old-source"]}},
        created_at=now,
        updated_at=now,
    )
    recent_msg = ChatMessage(
        id=str(ULID()),
        session_id=session.id,
        role="user",
        content="recent",
        payload={"scope": {"source_ids": ["new-source", "new-source"]}},
        created_at=now,
        updated_at=now,
    )
    db.add_all([session, old_msg, recent_msg])
    db.commit()
    db.refresh(session)

    assert _latest_session_source_ids(session) == ["new-source"]


def test_latest_session_job_ids_reads_recent_upload_receipt_payload(db):
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="job-context-test",
        mode="chat",
        asset="BTC",
        created_at=now,
        updated_at=now,
    )
    old_msg = ChatMessage(
        id=str(ULID()),
        session_id=session.id,
        role="assistant",
        content="old",
        payload={"upload_receipt": {"job_ids": ["old-job"]}},
        created_at=now,
        updated_at=now,
    )
    recent_msg = ChatMessage(
        id=str(ULID()),
        session_id=session.id,
        role="assistant",
        content="recent",
        payload={"upload_receipt": {"job_ids": ["new-job", "new-job"]}},
        created_at=now,
        updated_at=now,
    )
    db.add_all([session, old_msg, recent_msg])
    db.commit()
    db.refresh(session)

    assert _latest_session_job_ids(session) == ["new-job"]


def test_session_ingestion_context_endpoint_reads_persisted_upload_receipt(client, db):
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="ingestion context",
        mode="chat",
        asset="GENERAL",
        created_at=now,
        updated_at=now,
    )
    db.add_all([
        session,
        ChatMessage(
            id=str(ULID()),
            session_id=session.id,
            role="assistant",
            content="已接收资料",
            payload={
                "upload_receipt": {
                    "source_ids": ["source-ctx-1", "source-ctx-1"],
                    "job_ids": ["job-ctx-1"],
                },
                "scope": {"source_ids": ["source-ctx-1"]},
            },
            created_at=now,
            updated_at=now,
        ),
    ])
    db.commit()

    response = client.get(f"/api/v1/chat/sessions/{session.id}/ingestion-context")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session.id
    assert data["source_ids"] == ["source-ctx-1"]
    assert data["job_ids"] == ["job-ctx-1"]
    assert data["progress_urls"] == ["/api/v1/progress/job-ctx-1"]


def test_upload_receipt_updates_existing_batch_message(client, db):
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="receipt upsert",
        mode="chat",
        asset="GENERAL",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()

    first = client.post(
        f"/api/v1/chat/sessions/{session.id}/upload-receipt",
        json={
            "batch_id": "batch-upsert-1",
            "file_count": 1,
            "source_ids": ["source-upsert-1"],
            "job_ids": [],
            "status": "uploaded",
            "message": "uploaded",
        },
    )
    assert first.status_code == 200
    first_message_id = first.json()["message"]["id"]

    second = client.post(
        f"/api/v1/chat/sessions/{session.id}/upload-receipt",
        json={
            "batch_id": "batch-upsert-1",
            "file_count": 1,
            "source_ids": ["source-upsert-1"],
            "job_ids": ["job-upsert-1"],
            "status": "stored",
            "message": "stored",
        },
    )
    assert second.status_code == 200
    assert second.json()["message"]["id"] == first_message_id
    assert second.json()["upload_receipt"]["job_ids"] == ["job-upsert-1"]

    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).all()
    assert len(messages) == 1
    assert messages[0].payload["upload_receipt"]["status"] == "stored"
    assert messages[0].payload["upload_receipt"]["job_ids"] == ["job-upsert-1"]


def test_session_ingestion_context_filters_jobs_outside_current_sources(client, db):
    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="会话资料",
        raw_text="当前会话资料",
        language="zh",
        status="processing",
        word_count=6,
        created_at=now,
        updated_at=now,
    )
    other_source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="其他会话资料",
        raw_text="其他会话资料",
        language="zh",
        status="processing",
        word_count=6,
        created_at=now,
        updated_at=now,
    )
    current_job = JobRun(
        id=str(ULID()),
        job_type="ingestion",
        status="running",
        triggered_by="api",
        input_params={"source_id": source.id},
        result_summary={"source_id": source.id, "stage": "parse", "pct": 20},
        related_object_id=source.id,
        related_object_type="raw_source",
        created_at=now,
        updated_at=now,
    )
    foreign_job = JobRun(
        id=str(ULID()),
        job_type="ingestion",
        status="running",
        triggered_by="api",
        input_params={"source_id": other_source.id},
        result_summary={"source_id": other_source.id, "stage": "parse", "pct": 20},
        related_object_id=other_source.id,
        related_object_type="raw_source",
        created_at=now,
        updated_at=now,
    )
    session = ChatSession(
        id=str(ULID()),
        title="ingestion context scoped",
        mode="chat",
        asset="GENERAL",
        created_at=now,
        updated_at=now,
    )
    msg = ChatMessage(
        id=str(ULID()),
        session_id=session.id,
        role="assistant",
        content="已接收资料",
        payload={
            "upload_receipt": {
                "source_ids": [source.id],
                "job_ids": [foreign_job.id, current_job.id],
            },
            "scope": {"source_ids": [source.id]},
        },
        created_at=now,
        updated_at=now,
    )
    db.add_all([source, other_source, current_job, foreign_job, session, msg])
    db.commit()

    response = client.get(f"/api/v1/chat/sessions/{session.id}/ingestion-context")

    assert response.status_code == 200
    data = response.json()
    assert data["source_ids"] == [source.id]
    assert data["job_ids"] == [current_job.id]
    assert data["progress_urls"] == [f"/api/v1/progress/{current_job.id}"]


def test_upload_receipt_endpoint_persists_typed_receipt_and_context(client, db):
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="typed upload receipt",
        mode="chat",
        asset="GENERAL",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()

    response = client.post(
        f"/api/v1/chat/sessions/{session.id}/upload-receipt",
        json={
            "batch_id": "batch-typed-1",
            "file_count": 2,
            "source_ids": ["source-typed-1", "source-typed-1", "source-typed-2"],
            "job_ids": ["job-typed-1", "job-typed-2"],
            "status": "background_processing",
            "message": "后台处理中",
            "timed_out": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session.id
    assert data["message"]["role"] == "assistant"
    assert data["upload_receipt"]["source_ids"] == ["source-typed-1", "source-typed-2"]
    assert data["upload_receipt"]["job_ids"] == ["job-typed-1", "job-typed-2"]
    assert data["scope"] == {"mode": "selected_sources", "source_ids": ["source-typed-1", "source-typed-2"]}

    db.expire_all()
    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).all()
    assert len(messages) == 1
    payload = messages[0].payload
    assert payload["mode"] == "upload_receipt"
    assert payload["upload_receipt"]["batch_id"] == "batch-typed-1"
    assert payload["upload_receipt"]["timed_out"] is True
    assert payload["scope"]["source_ids"] == ["source-typed-1", "source-typed-2"]

    context_response = client.get(f"/api/v1/chat/sessions/{session.id}/ingestion-context")
    assert context_response.status_code == 200
    context = context_response.json()
    assert context["source_ids"] == ["source-typed-1", "source-typed-2"]
    assert context["job_ids"] == ["job-typed-1", "job-typed-2"]


def test_upload_receipt_endpoint_rejects_empty_sources(client, db):
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="empty upload receipt",
        mode="chat",
        asset="GENERAL",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()

    response = client.post(
        f"/api/v1/chat/sessions/{session.id}/upload-receipt",
        json={"source_ids": [" ", ""], "job_ids": ["job-only"]},
    )

    assert response.status_code == 422
    assert db.query(ChatMessage).filter(ChatMessage.session_id == session.id).count() == 0


def test_source_scope_includes_snippet_location(db):
    now = datetime.now(timezone.utc)
    source_id = str(ULID())
    raw_text = "开头无关内容。" * 20 + "MVP 演示必须保证上传解析、证据引用、回答可追溯。" + "后续补充。" * 20
    db.add(
        RawSource(
            id=source_id,
            source_type="note",
            title="snippet-source.txt",
            raw_text=raw_text,
            article_summary="MVP 演示主流程摘要",
            language="zh",
            status="completed",
            word_count=len(raw_text),
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    ctx = build_chat_context(
        db=db,
        query="MVP 演示需要保证什么",
        source_ids=[source_id, " ", source_id, None],
        include_market=False,
    )
    assert ctx.source_scope["requested_source_ids"] == [source_id]
    sources = ctx.source_scope["sources"]
    evidence = sources[0]["evidence"]

    assert evidence["span_type"] == "raw_text"
    assert "MVP 演示" in evidence["content"]
    assert isinstance(evidence["char_start"], int)
    assert isinstance(evidence["char_end"], int)
    assert evidence["char_end"] > evidence["char_start"]


def test_selected_source_chat_skips_unlinked_market_snapshot(db, monkeypatch):
    now = datetime.now(timezone.utc)
    source_id = str(ULID())
    db.add(
        RawSource(
            id=source_id,
            source_type="note",
            title="selected-market-boundary.txt",
            raw_text="选定资料只讨论证据边界，不包含实时市场行情。",
            language="zh",
            status="completed",
            word_count=24,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    market_call = MagicMock()
    monkeypatch.setattr(
        "app.mcps.market_data_mcp.MarketDataMCP.get_market_state",
        market_call,
    )

    context = build_chat_context(
        db=db,
        query="只根据选定资料分析 BTC",
        asset_filter="BTC",
        source_ids=[source_id],
        include_market=True,
    )

    market_call.assert_not_called()
    assert context.market_snapshot is None
    assert context.source_scope["matched_source_ids"] == [source_id]


def test_selected_source_local_path_does_not_restore_expired_claims(db, monkeypatch):
    now = datetime.now(timezone.utc)
    source_id = str(ULID())
    claim_id = str(ULID())
    source = RawSource(
        id=source_id,
        source_type="note",
        title="expired selected source",
        raw_text="An old directional statement that must remain archived as evidence.",
        language="en",
        status="completed",
        word_count=12,
        created_at=now,
        updated_at=now,
    )
    claim = ClaimCard(
        id=claim_id,
        summary="Expired bullish statement",
        full_quote="This statement expired yesterday.",
        claim_type="asset_view",
        stance="bullish",
        time_horizon="short",
        asset_scope=["TEST"],
        theme_scope=["expiry"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=[],
        confidence=0.9,
        source_trust_weight=0.9,
        is_contradicted=False,
        status="active",
        valid_until=now - timedelta(days=1),
        time_sensitive=True,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add_all([source, claim])
    db.flush()
    db.add(ClaimRawSourceLink(
        claim_id=claim_id,
        source_id=source_id,
        link_type="test",
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    monkeypatch.setattr(
        "app.services.chat_harness.balanced_semantic_search",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("small selected scope must stay local")),
    )

    context = build_chat_context(
        db=db,
        query="Should this old statement drive a current decision?",
        source_ids=[source_id],
        include_market=False,
    )

    assert context.search_tier == "selected-source-local"
    assert context.citations == []
    assert context.total_candidates_scanned == 0


def test_manual_chat_path_does_not_spend_an_llm_call_on_intent(client, monkeypatch):
    def unexpected_intent_llm(_self, _question: str):
        raise AssertionError("manual chat execution must use rule-only intent metadata")

    monkeypatch.setattr(
        "app.skills.intent_recognition_skill.IntentRecognitionSkill._llm_layer",
        unexpected_intent_llm,
    )
    session = client.post("/api/v1/chat/sessions", json={"title": "manual-chat-intent"}).json()

    response = client.post(
        f"/api/v1/chat/sessions/{session['id']}/chat",
        json={
            "message": "Please explain the evidence workflow in this manually selected chat mode.",
            "history": [],
            "scope_mode": "global",
        },
    )

    assert response.status_code == 200
    assert response.json()["intent_mode"] == "chat"


def test_source_scope_includes_published_at_metadata(db):
    now = datetime.now(timezone.utc)
    published_at = datetime(2026, 6, 20, tzinfo=timezone.utc)
    source_id = str(ULID())
    db.add(
        RawSource(
            id=source_id,
            source_type="note",
            title="dated-source.txt",
            raw_text="铜需求改善，分析师看涨铜价。",
            article_summary="铜价方向性观点",
            language="zh",
            status="completed",
            word_count=20,
            published_at=published_at,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    ctx = build_chat_context(
        db=db,
        query="下周铜价怎么看",
        source_ids=[source_id],
        include_market=False,
    )

    source = ctx.source_scope["sources"][0]
    assert source["published_at"].startswith("2026-06-20T00:00:00")
    assert "created_at" not in source


def test_claim_citation_prefers_persisted_evidence_span(db):
    now = datetime.now(timezone.utc)
    source_id = str(ULID())
    claim_id = str(ULID())
    span_id = str(ULID())
    db.add(
        RawSource(
            id=source_id,
            source_type="note",
            title="persisted-evidence-source.txt",
            raw_text="开头。MVP 演示必须保证引用能够定位到原文片段。结尾。",
            language="zh",
            status="completed",
            word_count=30,
            created_at=now,
            updated_at=now,
        )
    )
    claim = ClaimCard(
        id=claim_id,
        summary="MVP 演示必须保证引用能够定位到原文片段",
        full_quote="旧的兜底引用",
        claim_type="risk_warning",
        stance="neutral",
        time_horizon="short",
        asset_scope=[],
        theme_scope=["mvp"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=["test"],
        confidence=0.82,
        source_trust_weight=0.7,
        is_contradicted=False,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )
    span = EvidenceSpan(
        id=span_id,
        source_id=source_id,
        span_type="quote",
        content="MVP 演示必须保证引用能够定位到原文片段",
        char_start=3,
        char_end=25,
        confidence=0.98,
        extraction_method="exact_quote",
        metadata_={"claim_id": claim_id},
        created_at=now,
        updated_at=now,
    )
    db.add_all([claim, span])
    db.flush()
    db.add(ClaimEvidenceLink(
        claim_id=claim_id,
        evidence_span_id=span_id,
        relevance_score=0.98,
        created_at=now,
        updated_at=now,
    ))
    db.add(ClaimRawSourceLink(
        claim_id=claim_id,
        source_id=source_id,
        link_type="extracted_from",
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    db.refresh(claim)

    _block, citation = _build_claim_block(1, claim)
    ctx = build_chat_context(
        db=db,
        query="这个问题的关键词故意不匹配结构化观点",
        source_ids=[source_id],
        include_market=False,
    )

    assert citation.evidence_span_id == span_id
    assert citation.source_id == source_id
    assert citation.span_type == "quote"
    assert citation.char_start == 3
    assert citation.char_end == 25
    assert "定位到原文片段" in citation.short_quote
    assert ctx.citations
    assert ctx.citations[0].evidence_span_id == span_id


def test_claim_citation_falls_back_to_selected_raw_source_when_span_is_missing(db):
    now = datetime.now(timezone.utc)
    source_id = str(ULID())
    claim_id = str(ULID())
    source = RawSource(
        id=source_id,
        source_type="note",
        title="raw-link citation fallback",
        raw_text="Only the authoritative raw source link is available.",
        language="en",
        status="completed",
        word_count=7,
        created_at=now,
        updated_at=now,
    )
    claim = ClaimCard(
        id=claim_id,
        summary="The citation must retain its source identity",
        full_quote="Only the authoritative raw source link is available.",
        claim_type="risk_warning",
        stance="neutral",
        time_horizon="short",
        asset_scope=[],
        theme_scope=["citation"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=["test"],
        confidence=0.82,
        source_trust_weight=0.7,
        is_contradicted=False,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )
    claim.raw_source_links.append(
        ClaimRawSourceLink(
            claim_id=claim_id,
            source_id=source_id,
            link_type="extracted_from",
            created_at=now,
            updated_at=now,
        )
    )
    db.add_all([source, claim])
    db.commit()
    db.refresh(claim)

    _block, citation = _build_claim_block(1, claim, preferred_source_ids=[source_id])

    assert citation.source_id == source_id
    assert citation.evidence_span_id is None
    assert citation.page_or_timestamp is None
    assert citation.short_quote == claim.full_quote


def test_selected_source_claim_citation_prefers_evidence_from_selected_source(db, monkeypatch):
    now = datetime.now(timezone.utc)
    old_source_id = "00000000000000000000000001"
    selected_source_id = "99999999999999999999999999"
    claim_id = "11111111111111111111111111"
    old_span_id = "00000000000000000000000002"
    selected_span_id = "99999999999999999999999998"

    db.add_all([
        RawSource(
            id=old_source_id,
            source_type="note",
            title="old-duplicate-source.txt",
            raw_text="旧资料源包含同一条去重观点，但不是本次用户选定资料。",
            language="zh",
            status="completed",
            word_count=30,
            created_at=now,
            updated_at=now,
        ),
        RawSource(
            id=selected_source_id,
            source_type="note",
            title="selected-source.txt",
            raw_text="本次选定资料明确说明：多源引用必须来自选定资料源。",
            language="zh",
            status="completed",
            word_count=30,
            created_at=now,
            updated_at=now,
        ),
    ])
    claim = ClaimCard(
        id=claim_id,
        summary="多源引用必须来自选定资料源",
        full_quote="多源引用必须来自选定资料源",
        claim_type="risk_warning",
        stance="neutral",
        time_horizon="short",
        asset_scope=[],
        theme_scope=["scope"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=["test"],
        confidence=0.82,
        source_trust_weight=0.7,
        is_contradicted=False,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(claim)
    db.flush()
    db.add_all([
        EvidenceSpan(
            id=old_span_id,
            source_id=old_source_id,
            span_type="quote",
            content="旧资料源里的同一观点",
            char_start=0,
            char_end=10,
            confidence=0.91,
            extraction_method="exact_quote",
            metadata_={"claim_id": claim_id},
            created_at=now,
            updated_at=now,
        ),
        EvidenceSpan(
            id=selected_span_id,
            source_id=selected_source_id,
            span_type="quote",
            content="本次选定资料明确说明：多源引用必须来自选定资料源",
            char_start=0,
            char_end=24,
            confidence=0.99,
            extraction_method="exact_quote",
            metadata_={"claim_id": claim_id},
            created_at=now,
            updated_at=now,
        ),
    ])
    db.flush()
    db.add_all([
        ClaimEvidenceLink(
            claim_id=claim_id,
            evidence_span_id=old_span_id,
            relevance_score=0.91,
            created_at=now,
            updated_at=now,
        ),
        ClaimEvidenceLink(
            claim_id=claim_id,
            evidence_span_id=selected_span_id,
            relevance_score=0.99,
            created_at=now,
            updated_at=now,
        ),
        ClaimRawSourceLink(
            claim_id=claim_id,
            source_id=old_source_id,
            link_type="duplicate_of",
            created_at=now,
            updated_at=now,
        ),
        ClaimRawSourceLink(
            claim_id=claim_id,
            source_id=selected_source_id,
            link_type="extracted_from",
            created_at=now,
            updated_at=now,
        ),
    ])
    db.commit()

    def unexpected_remote_semantic_search(**_kwargs):
        raise AssertionError("small selected-source scopes should use local claim retrieval")

    monkeypatch.setattr(
        "app.services.chat_harness.sem_search.semantic_search",
        unexpected_remote_semantic_search,
    )

    ctx = build_chat_context(
        db=db,
        query="多源引用必须来自选定资料源",
        source_ids=[selected_source_id],
        include_market=False,
    )

    assert ctx.citations
    assert ctx.citations[0].source_id == selected_source_id
    assert ctx.citations[0].evidence_span_id == selected_span_id
    assert "本次选定资料" in ctx.citations[0].short_quote
