"""Chat Harness 单元测试：
1. stance-balanced 召回在一边倒场景下能注入对立观点
2. 不触发平衡时，结果保持按融合分数排序
3. context_block 包含 [N] 编号、trust/confidence 元信息、full_quote、条件字段
4. Citation 序号与 claim_id 一一对应
5. 空库场景返回空 context 但不崩溃
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from ulid import ULID

from app.schemas.claim_card import ClaimCard
from app.services.chat_harness import (
    build_chat_context,
    balanced_semantic_search,
    _stance_of,
    _rerank_score,
)


def _mk_claim(
    db, summary: str, stance: str, trust: float = 0.6,
    confidence: float = 0.8, full_quote: str = "",
    conditions: list | None = None, invalidations: list | None = None,
    asset_scope: list | None = None, time_horizon: str = "medium",
):
    now = datetime.now(timezone.utc)
    c = ClaimCard(
        id=str(ULID()), summary=summary,
        claim_type="asset_view", stance=stance,
        time_horizon=time_horizon,
        asset_scope=asset_scope or ["BTC"], theme_scope=[], geographic_scope=[],
        conditions=conditions or [], invalidation_conditions=invalidations or [],
        reasoning_basis=[],
        full_quote=full_quote or summary,
        confidence=confidence, source_trust_weight=trust,
        is_contradicted=False, status="active", version=1,
        created_at=now, updated_at=now,
    )
    db.add(c); db.commit(); db.refresh(c)
    return c


# ── 基础工具测试 ──────────────────────────────────────────
def test_stance_of_normalization():
    class _C: pass
    c = _C()
    c.stance = "bull"
    assert _stance_of(c) == "bullish"
    c.stance = "bearish"
    assert _stance_of(c) == "bearish"
    c.stance = "conditional"
    assert _stance_of(c) == "conditional"
    c.stance = ""
    assert _stance_of(c) == "neutral"
    c.stance = None
    assert _stance_of(c) == "neutral"


def test_rerank_score_weights_trust_and_confidence():
    class _C: pass
    high = _C(); high.source_trust_weight = 0.9; high.confidence = 0.9
    low = _C(); low.source_trust_weight = 0.1; low.confidence = 0.1
    # 同一 semantic rank 下，高信任高置信得分更高
    assert _rerank_score(high, 0, 10) > _rerank_score(low, 0, 10)
    # semantic rank 越靠前（idx 小）得分越高
    assert _rerank_score(high, 0, 10) > _rerank_score(high, 9, 10)


# ── balanced_semantic_search ─────────────────────────────
def test_balanced_injects_opposite_when_one_sided(db, monkeypatch):
    """若初筛全是 bullish 且池中有 bearish，应该注入至少 1 条 bearish。"""
    # 建 6 bullish + 3 bearish
    bull_ids = []
    for i in range(6):
        c = _mk_claim(db, f"比特币将上涨 {i}", "bullish", trust=0.8 - i * 0.05)
        bull_ids.append(c.id)
    bear_ids = []
    for i in range(3):
        c = _mk_claim(db, f"比特币将下跌 {i}", "bearish", trust=0.55 - i * 0.05)
        bear_ids.append(c.id)

    # 打桩 semantic_search：按加入顺序返回（先 bullish 再 bearish）
    from app.services import chat_harness
    all_claims = db.query(ClaimCard).order_by(ClaimCard.created_at.asc()).all()
    monkeypatch.setattr(
        chat_harness.sem_search, "semantic_search",
        lambda **kw: all_claims[: kw.get("limit", 10)]
    )

    claims, dist, balanced, scanned = balanced_semantic_search(
        db=db, query="BTC 怎么看", limit=5,
    )
    assert scanned > 0
    # 应该注入了至少 1 条 bearish
    assert balanced is True
    assert dist.get("bearish", 0) >= 1, f"Expected bearish injected, got dist={dist}"
    assert any(_stance_of(c) == "bearish" for c in claims)


def test_balanced_noop_when_already_diverse(db, monkeypatch):
    """若初筛已有 bullish+bearish 混合，不应触发平衡。"""
    _mk_claim(db, "上涨 A", "bullish", trust=0.8)
    _mk_claim(db, "上涨 B", "bullish", trust=0.7)
    _mk_claim(db, "下跌 A", "bearish", trust=0.7)
    _mk_claim(db, "下跌 B", "bearish", trust=0.6)

    from app.services import chat_harness
    all_claims = db.query(ClaimCard).all()
    monkeypatch.setattr(
        chat_harness.sem_search, "semantic_search",
        lambda **kw: all_claims[: kw.get("limit", 10)]
    )

    claims, dist, balanced, _ = balanced_semantic_search(
        db=db, query="BTC 怎么看", limit=4,
    )
    assert balanced is False
    assert dist.get("bullish", 0) >= 1
    assert dist.get("bearish", 0) >= 1


def test_balanced_noop_when_no_opposite_available(db, monkeypatch):
    """若全库只有 bullish，无法平衡，应平静返回不崩。"""
    for i in range(4):
        _mk_claim(db, f"上涨 {i}", "bullish")

    from app.services import chat_harness
    all_claims = db.query(ClaimCard).all()
    monkeypatch.setattr(
        chat_harness.sem_search, "semantic_search",
        lambda **kw: all_claims[: kw.get("limit", 10)]
    )

    claims, dist, balanced, _ = balanced_semantic_search(
        db=db, query="BTC", limit=4,
    )
    assert balanced is False
    assert dist.get("bullish", 0) == 4
    assert dist.get("bearish", 0) == 0


# ── build_chat_context ───────────────────────────────────
def test_context_block_contains_citations_and_metadata(db, monkeypatch):
    """context_block 应包含 [N] 编号 + 信任/置信 + full_quote + 失效条件。"""
    c = _mk_claim(
        db, summary="BTC 短期看多",
        stance="bullish", trust=0.75, confidence=0.9,
        full_quote="核心逻辑：ETF 流入 + 现货溢价持续扩大",
        conditions=["ETF 日净流入 > 200M"],
        invalidations=["跌破 60000 美元"],
        time_horizon="short",
    )

    from app.services import chat_harness
    monkeypatch.setattr(
        chat_harness.sem_search, "semantic_search",
        lambda **kw: [c],
    )
    monkeypatch.setattr(
        chat_harness.sem_search, "get_last_search_tier",
        lambda: "cosine",
    )
    # 不调用市场快照（避免 MCP 触发）
    ctx = build_chat_context(
        db=db, query="BTC 短期如何", asset_filter=None, limit=5, include_market=False,
    )

    assert "[1]" in ctx.context_block
    assert "看多" in ctx.context_block
    assert "信任 0.75" in ctx.context_block
    assert "置信 0.90" in ctx.context_block
    assert "原文引用" in ctx.context_block
    assert "ETF 流入" in ctx.context_block
    assert "成立条件" in ctx.context_block
    assert "失效条件" in ctx.context_block
    assert "跌破 60000" in ctx.context_block

    # Citation 对齐
    assert len(ctx.citations) == 1
    assert ctx.citations[0].index == 1
    assert ctx.citations[0].claim_id == c.id
    assert ctx.citations[0].stance == "bullish"
    assert ctx.citations[0].trust_weight == pytest.approx(0.75, 0.01)


def test_empty_library_returns_empty_context(db, monkeypatch):
    from app.services import chat_harness
    monkeypatch.setattr(chat_harness.sem_search, "semantic_search", lambda **kw: [])
    monkeypatch.setattr(chat_harness.sem_search, "get_last_search_tier", lambda: "keyword")
    ctx = build_chat_context(
        db=db, query="任意", asset_filter=None, limit=5, include_market=False,
    )
    assert ctx.context_block == ""
    assert ctx.citations == []
    assert ctx.stance_distribution == {}
    assert ctx.balanced_applied is False


def test_citation_indices_are_1_based_and_contiguous(db, monkeypatch):
    """多条结果时 [1][2][3]... 编号应连续且从 1 开始。"""
    claims = [
        _mk_claim(db, f"obs {i}", "bullish" if i % 2 == 0 else "bearish")
        for i in range(4)
    ]
    from app.services import chat_harness
    monkeypatch.setattr(chat_harness.sem_search, "semantic_search", lambda **kw: claims)
    monkeypatch.setattr(chat_harness.sem_search, "get_last_search_tier", lambda: "cosine")

    ctx = build_chat_context(
        db=db, query="any", asset_filter=None, limit=10, include_market=False,
    )
    indices = [c.index for c in ctx.citations]
    assert indices == list(range(1, len(ctx.citations) + 1))
    # context_block 应能找到每个 [N]
    for i in indices:
        assert f"[{i}]" in ctx.context_block
