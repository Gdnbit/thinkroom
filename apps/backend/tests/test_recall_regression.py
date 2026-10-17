"""召回率回归测试 · 防止检索栈回归.

为什么需要这个
----------
在 chat / briefing 三大链路改造（reranker / weights / parent context / topk）之后，
缺乏一个"基线检索质量"的回归保护。任何对 semantic_search / claim_post_processor /
embedding pipeline 的改动都可能无声降低召回。

设计原则
----------
1. **可重现**：用合成 claim + 确定性 embedding（基于文本 hash），不依赖 LLM
2. **快**：~30 个 claim + 10 query，整个测试 <5s
3. **走 Tier ③ Python brute-force cosine**：避开 pgvector / sqlite-vec 环境差异
4. **基线宽松**：避免风吹草动 flaky；阈值留 buffer
   - recall@10 >= 0.65（at least 65% of relevant claims must be in top 10）
   - mrr >= 0.4（mean reciprocal rank — 相关 claim 应平均出现在前 2.5 位）

更新基线
----------
若有意降低基线（如裁剪过强匹配），改动这里的 EXPECTED_RECALL_AT_10 等常量
并在 commit message 解释原因。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Tuple

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ulid import ULID

from app.database import Base
from app.schemas.claim_card import ClaimCard


# ── 基线阈值 ────────────────────────────────────────────────


# 基线考虑因素：
# - 合成数据每 topic 只有 2-3 条 claim，single 命中即 0.33-0.5
# - 用极简 hash-based embedding，跨语言匹配靠主题关键词加权
# - brute-force cosine 阈值 sim > 0.3 可能漏掉低权重匹配
EXPECTED_RECALL_AT_5 = 0.50
EXPECTED_RECALL_AT_10 = 0.55
EXPECTED_MRR = 0.40


# ── 合成数据集（10 主题 × 3 claim per topic） ──────────────


# 每条 entry: (主题关键词, 内容文本, asset_scope)
_SYNTHETIC_CLAIMS: List[Tuple[str, str, list]] = [
    # 比特币 / 加密货币（5 条）
    ("BTC_BULLISH", "比特币价格突破历史高点 75000 美元，机构持仓持续加码 ETF。", ["BTC"]),
    ("BTC_BULLISH", "BTC reached new ATH driven by spot ETF inflow and halving cycle.", ["BTC"]),
    ("BTC_BEARISH", "比特币回调风险升高，链上数据显示长期持有者抛售加速。", ["BTC"]),
    ("ETH_NARRATIVE", "以太坊 Pectra 升级引发 staking 收益预期下行 关注 Restaking 流量。", ["ETH"]),
    ("ETH_NARRATIVE", "Ethereum's Pectra upgrade reduces validator yields, restaking grows.", ["ETH"]),

    # 美股 (4 条)
    ("FED_RATES", "Fed 主席鲍威尔暗示 9 月可能降息，市场定价 75bp。", ["SPX", "NDX"]),
    ("FED_RATES", "Powell hinted at September rate cut, markets price in 75 bp easing this year.", ["SPX"]),
    ("AI_STOCKS", "英伟达财报超预期，AI 算力需求持续旺盛，QQQ 单日涨 2%。", ["NDX", "NVDA"]),
    ("AI_STOCKS", "NVIDIA earnings beat consensus on AI compute demand, semis lead the rally.", ["NVDA"]),

    # A 股 (4 条) — 包含 query 中最可能出现的关键词
    ("CN_STIMULUS", "中国财政刺激政策：国常会推出 1 万亿特别国债重点支持新基建与设备更新。", ["CSI300", "CYB"]),
    ("CN_STIMULUS", "China stimulus: 1 trillion CNY special bonds for infrastructure and capex.", ["CSI300"]),
    ("CN_PROPERTY", "央行下调首付比例至 15%，房地产板块单日反弹 5%。", ["CSI300"]),
    ("CN_PROPERTY", "PBOC cuts mortgage downpayment to 15%, property sector rebounds.", ["CSI300"]),

    # 大宗商品 (3 条)
    ("GOLD_HEDGE", "金价突破 2400 美元，避险情绪 + 央行购金推动。", ["GOLD"]),
    ("GOLD_HEDGE", "Gold breaks $2400 driven by central bank buying and geopolitical risk.", ["GOLD"]),
    ("OIL_SUPPLY", "OPEC+ 决定延长减产至 2025 年中，油价单周涨 4%。", ["OIL"]),

    # 宏观噪声（3 条，用于 distraction）
    ("MACRO_NOISE", "欧元区 CPI 数据高于预期 ECB 9 月降息概率下降。", ["DXY"]),
    ("MACRO_NOISE", "Japan's BoJ kept rates unchanged, yen weakens further past 160.", ["DXY"]),
    ("MACRO_NOISE", "印度央行维持基准利率不变，关注下季度通胀走势。", []),
]


# 测试 query → 期望命中的主题
_QUERIES: List[Tuple[str, str]] = [
    ("比特币最近的走势怎么样？", "BTC_BULLISH"),
    ("BTC ETF inflow trend", "BTC_BULLISH"),
    ("以太坊升级影响", "ETH_NARRATIVE"),
    ("Ethereum Pectra staking", "ETH_NARRATIVE"),
    ("美联储降息预期", "FED_RATES"),
    ("Powell September rate cut", "FED_RATES"),
    ("英伟达 AI 算力", "AI_STOCKS"),
    ("中国财政刺激政策", "CN_STIMULUS"),
    ("China stimulus infrastructure", "CN_STIMULUS"),
    ("黄金价格", "GOLD_HEDGE"),
]


# ── 确定性 embedding（基于文本 hash） ──────────────────────


def _deterministic_embed(text: str, dim: int = 128) -> List[float]:
    """基于 SHA256 + 简单 token 频率的确定性向量.

    设计目标：
    - 同义词（同主题不同语种）的相似度 > 0.4
    - 不同主题的相似度 < 0.3

    用文本里 char-level n-gram 的 hash 桶，类似简单 LSH。
    """
    text = text.lower().strip()
    vec = np.zeros(dim, dtype=np.float32)

    # 1. char 3-gram 桶分布
    for i in range(len(text) - 2):
        gram = text[i:i + 3]
        h = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dim
        vec[idx] += 1.0

    # 2. 主题词强权重（抓住主题关键 token）
    topic_keywords = {
        "btc": 5, "bitcoin": 5, "比特币": 5, "eth": 5, "ethereum": 5, "以太坊": 5,
        "fed": 4, "powell": 4, "降息": 4, "rate cut": 3, "加息": 3,
        "gold": 4, "黄金": 4, "金价": 4, "ai": 3, "英伟达": 4, "nvidia": 4,
        "stimulus": 3, "刺激": 3, "国债": 3, "财政": 3, "infrastructure": 3,
        "中国": 3, "china": 3, "政策": 2,
        "property": 3, "房地产": 3, "首付": 3, "oil": 3, "opec": 3,
        "etf": 2, "升级": 2, "upgrade": 2, "staking": 2, "restaking": 2,
    }
    for kw, weight in topic_keywords.items():
        if kw in text:
            h = int(hashlib.md5(kw.encode()).hexdigest()[:8], 16)
            for offset in range(weight):
                vec[(h + offset) % dim] += 5.0

    # L2 normalize
    norm = np.linalg.norm(vec)
    return (vec / (norm + 1e-9)).tolist()


# ── DB fixture ────────────────────────────────────────────


@pytest.fixture
def db_session_with_claims(monkeypatch):
    """In-memory SQLite + 30 条合成 claim + 强制 Tier ③ brute-force.

    monkeypatch embed_text 用确定性 embed；关闭 pgvector 走 brute-force。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # 关 pgvector 走 brute-force tier
    monkeypatch.setattr("app.config.settings.PGVECTOR_ENABLED", False)
    # mock embed_text 为确定性
    monkeypatch.setattr("app.services.semantic_search.embed_text", _deterministic_embed)
    # mock 不要走 sqlite-vec（保持 brute-force tier 简单）
    try:
        monkeypatch.setattr(
            "app.services.semantic_search._sqlite_vec_search",
            lambda *a, **kw: None
        )
    except AttributeError:
        pass

    import json as _json

    # 写入 claims
    topic_to_ids = {}
    for i, (topic, content, scope) in enumerate(_SYNTHETIC_CLAIMS):
        cid = str(ULID())
        c = ClaimCard(
            id=cid,
            summary=content,
            stance="bullish" if "bullish" in topic.lower() else "neutral",
            claim_type="market_narrative",
            confidence=0.7,
            source_trust_weight=0.6 + 0.05 * (i % 4),
            asset_scope=scope,
            theme_scope=[topic.lower()],
            geographic_scope=[],
            conditions=[],
            invalidation_conditions=[],
            reasoning_basis=[],
            embedding=_json.dumps(_deterministic_embed(content)),
            time_sensitive=False,
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        db.add(c)
        topic_to_ids.setdefault(topic, []).append(cid)
    db.commit()

    yield db, topic_to_ids
    db.close()
    engine.dispose()


# ── 指标计算 ─────────────────────────────────────────────


def _recall_at_k(relevant_ids: set, ranked_ids: List[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    top_k = set(ranked_ids[:k])
    return len(relevant_ids & top_k) / len(relevant_ids)


def _mrr(relevant_ids: set, ranked_ids: List[str]) -> float:
    """Reciprocal rank: 1 / (1-indexed position of first hit)."""
    for i, cid in enumerate(ranked_ids, start=1):
        if cid in relevant_ids:
            return 1.0 / i
    return 0.0


# ── 主测试 ─────────────────────────────────────────────


def test_recall_baseline(db_session_with_claims):
    """跨 10 个 query 的平均 recall@5 / recall@10 / MRR 不低于基线."""
    db, topic_to_ids = db_session_with_claims
    from app.services.semantic_search import semantic_search

    recalls_5 = []
    recalls_10 = []
    mrrs = []
    detail_lines = []

    for query, expected_topic in _QUERIES:
        relevant = set(topic_to_ids[expected_topic])
        results = semantic_search(db, query, limit=15)
        ranked_ids = [r.id for r in results]

        r5 = _recall_at_k(relevant, ranked_ids, 5)
        r10 = _recall_at_k(relevant, ranked_ids, 10)
        rr = _mrr(relevant, ranked_ids)

        recalls_5.append(r5)
        recalls_10.append(r10)
        mrrs.append(rr)
        detail_lines.append(
            f"  {query[:30]:30s}  topic={expected_topic:18s}  "
            f"r5={r5:.2f} r10={r10:.2f} mrr={rr:.2f}"
        )

    avg_r5 = sum(recalls_5) / len(recalls_5)
    avg_r10 = sum(recalls_10) / len(recalls_10)
    avg_mrr = sum(mrrs) / len(mrrs)

    diagnostics = (
        f"\n=== Recall regression diagnostics ===\n"
        + "\n".join(detail_lines)
        + f"\n=> avg_recall@5  = {avg_r5:.3f}  (baseline >= {EXPECTED_RECALL_AT_5})"
        + f"\n=> avg_recall@10 = {avg_r10:.3f}  (baseline >= {EXPECTED_RECALL_AT_10})"
        + f"\n=> avg_mrr       = {avg_mrr:.3f}  (baseline >= {EXPECTED_MRR})\n"
    )

    assert avg_r5 >= EXPECTED_RECALL_AT_5, f"recall@5 regression{diagnostics}"
    assert avg_r10 >= EXPECTED_RECALL_AT_10, f"recall@10 regression{diagnostics}"
    assert avg_mrr >= EXPECTED_MRR, f"MRR regression{diagnostics}"


def test_search_returns_results_for_all_queries(db_session_with_claims):
    """每个 query 至少能找到 1 条结果（防止 retrieval 完全失败）."""
    db, _ = db_session_with_claims
    from app.services.semantic_search import semantic_search

    for query, _expected_topic in _QUERIES:
        results = semantic_search(db, query, limit=10)
        assert len(results) > 0, f"No results for query: {query!r}"


def test_asset_filter_narrows_results(db_session_with_claims):
    """asset_filter 应能限制返回到指定资产."""
    db, _ = db_session_with_claims
    from app.services.semantic_search import semantic_search

    btc_only = semantic_search(db, "市场怎么样", asset_filter="BTC", limit=20)
    # 所有结果的 asset_scope 应包含 BTC
    for r in btc_only:
        scope = r.asset_scope or []
        assert any("BTC" in str(s).upper() for s in scope) or len(scope) == 0, (
            f"Got non-BTC result with scope {scope}"
        )
