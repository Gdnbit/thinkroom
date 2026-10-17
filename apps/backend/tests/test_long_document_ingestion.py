from __future__ import annotations

from datetime import datetime, timezone

from ulid import ULID

from app.schemas.claim_card import ClaimCard
from app.schemas.claim_card import ClaimEvidenceLink
from app.schemas.evidence_span import EvidenceSpan
from app.schemas.raw_source import RawSource
from app.skills.claim_extraction_skill import ClaimExtractionOutput, ExtractedClaim
from app.utils.text_stats import build_source_status_payload
from app.workflows.ingestion_workflow import IngestionWorkflow


def test_long_document_ingestion_defers_claim_extraction_without_external_models(db):
    now = datetime.now(timezone.utc)
    long_text = (
        "2026年5月13日，原油市场维持震荡，库存、美元指数和地缘风险共同影响价格。"
        "该材料用于验证长文档解析、摘要和延后观点抽取状态。"
        "\n\n"
    ) * 2600
    source = RawSource(
        id=str(ULID()),
        source_type="report",
        title="长文档回归测试",
        raw_text=long_text,
        speaker_hint="测试来源",
        theme_hint="长文档测试",
        status="pending",
        language="zh",
        word_count=len(long_text),
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()

    result = IngestionWorkflow(db).process_source(source.id)

    db.refresh(source)
    claim_count = db.query(ClaimCard).filter(ClaimCard.summary.like("%长文档回归测试%")).count()
    status_payload = build_source_status_payload(source, claim_count=0)

    assert result["success"] is True
    assert result["claims_extracted"] == 0
    assert result["warnings"] == ["long_document_claim_extraction_deferred"]
    assert result["long_document_mode"] == "parse_store_mvp"
    assert result["original_chunks"] > 12
    assert result["text_chars"] == len(long_text)
    assert result["article_summary_chars"] == len(source.article_summary)
    assert result["parse_diagnostics"]["status"] == "completed"
    assert result["parse_diagnostics"]["reason_code"] == "text_extracted"
    assert result["parse_diagnostics"]["document_parse"]["text_chars"] == len(long_text)
    assert result["parse_diagnostics"]["document_parse"]["segment_count"] == 1
    assert source.status == "completed"
    assert source.word_count == len(long_text)
    assert source.article_summary.startswith("【长文档摘要】")
    assert claim_count == 0
    assert status_payload["long_document_flag"] is True
    assert status_payload["claim_extraction_status"] == "deferred"
    assert status_payload["processing_stage"] == "summarized"


def test_ingestion_creates_evidence_span_for_extracted_claim(db, monkeypatch):
    exact_quote = "WTI crude remains range-bound unless inventories draw down for two consecutive weeks."
    source_text = (
        "Market note on 2026-05-13. "
        f"{exact_quote} "
        "A stronger dollar would invalidate the near-term bullish setup."
    )
    now = datetime.now(timezone.utc)
    source = RawSource(
        id="src_test_evidence_001",
        source_type="note",
        title="Evidence span regression",
        raw_text=source_text,
        speaker_hint="Evidence Analyst",
        theme_hint="Oil market",
        status="pending",
        language="en",
        word_count=len(source_text),
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()

    def fake_execute(_self, input_data):
        return ClaimExtractionOutput(
            raw_source_id=input_data.raw_source_id,
            claims=[
                ExtractedClaim(
                    summary="WTI crude is range-bound unless inventories keep falling",
                    contextual_summary="A 2026-05-13 market note discussing near-term WTI crude drivers.",
                    claim_type="asset_view",
                    stance="conditional",
                    time_horizon="short",
                    asset_scope=["WTI", "OIL"],
                    theme_scope=["inventories", "dollar"],
                    confidence=0.78,
                    reasoning_basis=["inventory drawdowns are required"],
                    conditions=["inventories draw down for two consecutive weeks"],
                    invalidation_conditions=["stronger dollar"],
                    full_quote=exact_quote,
                )
            ],
            total_extracted=1,
            is_mock=False,
            skipped_count=0,
            warnings=[],
            source_mode="live",
        )

    monkeypatch.setattr("app.skills.claim_extraction_skill.ClaimExtractionSkill.execute", fake_execute)
    monkeypatch.setattr("app.services.embedding.embed_text", lambda _text: None)
    monkeypatch.setattr("app.services.semantic_search.index_claim", lambda _db, _claim: None)
    monkeypatch.setattr("app.services.semantic_search.flush_pending_vec_upserts", lambda: None)
    monkeypatch.setattr(
        "app.workflows.ingestion_workflow.IngestionWorkflow._generate_article_summary",
        lambda _self, text: text[:180],
    )

    result = IngestionWorkflow(db).process_source(source.id)

    assert result["success"] is True
    assert result["claims_extracted"] == 1
    claim = db.query(ClaimCard).filter(ClaimCard.summary.like("WTI crude%")).one()
    evidence = db.query(EvidenceSpan).filter(EvidenceSpan.source_id == source.id).one()
    link = db.query(ClaimEvidenceLink).filter(ClaimEvidenceLink.claim_id == claim.id).one()

    assert evidence.content == exact_quote
    assert evidence.extraction_method in {"exact_quote", "normalized_quote"}
    assert evidence.char_start == source_text.index(exact_quote)
    assert evidence.char_end == evidence.char_start + len(exact_quote)
    assert evidence.confidence >= 0.9
    assert link.evidence_span_id == evidence.id
