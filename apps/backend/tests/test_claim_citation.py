"""Chat 引用 [N] 反查 raw_source 测试 · GET /api/v1/claims/{id}/citation."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from ulid import ULID


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.config.settings.APP_API_KEY", "")
    from app.database import engine, Base as MainBase
    MainBase.metadata.drop_all(engine)
    MainBase.metadata.create_all(engine)
    from app.main import app
    with TestClient(app) as c:
        yield c
    # teardown：确保不污染下一个用 conftest db fixture 的测试
    MainBase.metadata.drop_all(engine)


def _setup_full_chain(client, with_evidence: bool = False, with_speaker: bool = False):
    """创建完整 claim → raw_source → evidence_span 链."""
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink, ClaimEvidenceLink
    from app.schemas.evidence_span import EvidenceSpan
    from app.schemas.speaker_profile import SpeakerProfile

    db = SessionLocal()

    speaker_id = None
    if with_speaker:
        sp = SpeakerProfile(
            id=str(ULID()),
            name="张三",
            aliases=[],
            domain_tags=["macro", "fixed_income"],
            trust_weight=0.85,
            applicable_domains=[],
        )
        db.add(sp)
        db.flush()
        speaker_id = sp.id

    src = RawSource(
        id=str(ULID()),
        source_type="article",
        title="2026 年 4 月美联储议息会议解读",
        original_url="https://example.com/fomc-april-2026",
        publisher="Bloomberg",
        raw_text="美联储主席鲍威尔在议息会议后的新闻发布会上表示...（此处为长文，约 2000 字。" * 60,
        language="zh",
        speaker_hint="张三",
        published_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        status="completed",
    )
    db.add(src)
    db.flush()

    claim = ClaimCard(
        id=str(ULID()),
        summary="美联储 9 月降息概率上升至 75%。",
        full_quote="鲍威尔暗示 9 月可能降息 25 bp。",
        claim_type="macro_view",
        stance="bullish",
        time_horizon="short",
        asset_scope=["SPX", "NDX"],
        theme_scope=["fed_policy"],
        geographic_scope=["US"],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=["powell_speech"],
        confidence=0.8,
        source_trust_weight=0.7,
        time_sensitive=True,
        speaker_id=speaker_id,
        status="active",
    )
    db.add(claim)
    db.flush()

    db.add(ClaimRawSourceLink(
        claim_id=claim.id, source_id=src.id, link_type="extracted_from"
    ))

    span_id = None
    if with_evidence:
        span = EvidenceSpan(
            id=str(ULID()),
            source_id=src.id,
            span_type="quote",
            content="鲍威尔：9 月可能降息 25bp",
            page_or_timestamp="paragraph 3",
            char_start=120,
            char_end=180,
            confidence=0.95,
            extraction_method="llm",
        )
        db.add(span)
        db.flush()
        span_id = span.id
        db.add(ClaimEvidenceLink(
            claim_id=claim.id, evidence_span_id=span.id, relevance_score=0.95
        ))

    db.commit()
    result = {
        "claim_id": claim.id,
        "source_id": src.id,
        "speaker_id": speaker_id,
        "evidence_span_id": span_id,
    }
    db.close()
    return result


# ── 基础 ────────────────────────────────────────────────


def test_citation_returns_full_chain(client):
    ids = _setup_full_chain(client, with_evidence=True, with_speaker=True)
    resp = client.get(f"/api/v1/claims/{ids['claim_id']}/citation")
    assert resp.status_code == 200
    body = resp.json()

    # claim 部分
    assert body["claim"]["id"] == ids["claim_id"]
    assert "美联储" in body["claim"]["summary"]
    assert body["claim"]["asset_scope"] == ["SPX", "NDX"]
    assert body["claim"]["confidence"] == 0.8

    # speaker 部分
    assert body["speaker"]["name"] == "张三"
    assert body["speaker"]["trust_weight"] == 0.85

    # sources 部分
    assert len(body["sources"]) == 1
    src_data = body["sources"][0]
    assert src_data["title"].startswith("2026")
    assert src_data["url"].startswith("https")
    assert "美联储" in src_data["excerpt"]
    assert src_data["excerpt_truncated"] is True  # 原文 > 800 字符
    assert src_data["link_type"] == "extracted_from"

    # evidence 部分
    assert len(body["evidence_spans"]) == 1
    ev = body["evidence_spans"][0]
    assert ev["span_type"] == "quote"
    assert ev["char_start"] == 120
    assert ev["char_end"] == 180
    assert ev["relevance_score"] == 0.95


def test_citation_without_evidence_or_speaker(client):
    """最简 claim：无 evidence_spans + 无 speaker."""
    ids = _setup_full_chain(client)
    resp = client.get(f"/api/v1/claims/{ids['claim_id']}/citation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["speaker"] is None
    assert body["evidence_spans"] == []
    assert len(body["sources"]) == 1


def test_citation_unknown_claim_404(client):
    resp = client.get("/api/v1/claims/01H0XBOGUS00000000000000000/citation")
    assert resp.status_code == 404


def test_citation_excerpt_chars_param(client):
    """excerpt_chars 控制摘要长度."""
    ids = _setup_full_chain(client)
    resp = client.get(f"/api/v1/claims/{ids['claim_id']}/citation?excerpt_chars=200")
    assert resp.status_code == 200
    body = resp.json()
    src = body["sources"][0]
    assert len(src["excerpt"]) <= 200
    assert src["excerpt_truncated"] is True


def test_citation_excerpt_chars_validation(client):
    """超出范围的 excerpt_chars 应 422."""
    resp = client.get("/api/v1/claims/whatever/citation?excerpt_chars=10")
    assert resp.status_code == 422
    resp2 = client.get("/api/v1/claims/whatever/citation?excerpt_chars=99999")
    assert resp2.status_code == 422


def test_citation_short_text_no_truncation(client):
    """raw_text 较短时 excerpt_truncated=False."""
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

    db = SessionLocal()
    src = RawSource(
        id=str(ULID()), source_type="note",
        raw_text="短笔记一句话。", language="zh", status="completed",
    )
    db.add(src)
    claim = ClaimCard(
        id=str(ULID()), summary="一个短判断", stance="neutral",
        claim_type="market_narrative", confidence=0.7,
        asset_scope=[], theme_scope=[], geographic_scope=[],
        conditions=[], invalidation_conditions=[], reasoning_basis=[],
        time_sensitive=False, status="active",
    )
    db.add(claim)
    db.flush()
    db.add(ClaimRawSourceLink(claim_id=claim.id, source_id=src.id, link_type="extracted_from"))
    db.commit()
    cid = claim.id
    db.close()

    resp = client.get(f"/api/v1/claims/{cid}/citation?excerpt_chars=800")
    assert resp.status_code == 200
    src_data = resp.json()["sources"][0]
    assert src_data["excerpt_truncated"] is False
    assert src_data["excerpt"] == "短笔记一句话。"


def test_citation_multiple_sources(client):
    """同一 claim 链接到多个 source（如 cross-validated claim）."""
    from app.database import SessionLocal
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

    db = SessionLocal()
    s1 = RawSource(id=str(ULID()), source_type="article", title="src 1", raw_text="a", language="zh", status="completed")
    s2 = RawSource(id=str(ULID()), source_type="tweet", title="src 2", raw_text="b", language="zh", status="completed")
    db.add_all([s1, s2])
    claim = ClaimCard(
        id=str(ULID()), summary="cross-validated", stance="neutral",
        claim_type="market_narrative", confidence=0.9,
        asset_scope=[], theme_scope=[], geographic_scope=[],
        conditions=[], invalidation_conditions=[], reasoning_basis=[],
        time_sensitive=False, status="active",
    )
    db.add(claim)
    db.flush()
    db.add_all([
        ClaimRawSourceLink(claim_id=claim.id, source_id=s1.id, link_type="extracted_from"),
        ClaimRawSourceLink(claim_id=claim.id, source_id=s2.id, link_type="cross_referenced"),
    ])
    db.commit()
    cid = claim.id
    db.close()

    resp = client.get(f"/api/v1/claims/{cid}/citation")
    assert resp.status_code == 200
    sources = resp.json()["sources"]
    assert len(sources) == 2
    link_types = {s["link_type"] for s in sources}
    assert link_types == {"extracted_from", "cross_referenced"}
