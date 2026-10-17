"""Signal Scorer — 量化信号预评分引擎

在 LLM 融合之前，先用规则引擎对各维度信号进行量化评分。
输出结构化的 SignalScorecard，作为 LLM 的定量输入补充。

评分维度：
1. 私域观点（Claim）→ 加权情感得分 + 一致性指标
2. 市场状态（Market）→ 技术面综合得分
3. 资金信号（Flow）→ 情绪极端度
4. 事件（Event）→ 加权影响力得分

所有得分归一化到 [-1.0, +1.0]：
  -1.0 = 极度看空 / 极高风险
  +1.0 = 极度看多 / 极低风险
   0.0 = 中性

输出还包含 signal_conflict 冲突检测标志。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DimensionScore:
    """单维度评分"""
    dimension: str          # claim | market | flow | event
    score: float            # -1.0 ~ +1.0
    confidence: float       # 0.0 ~ 1.0 该维度数据充分度
    data_points: int        # 该维度有效数据条目数
    summary: str            # 一句话描述
    details: dict = field(default_factory=dict)


@dataclass
class SignalScorecard:
    """量化信号评分卡"""
    asset: str
    dimensions: List[DimensionScore]
    composite_score: float          # 加权综合得分 -1.0 ~ +1.0
    composite_confidence: float     # 综合数据充分度 0.0 ~ 1.0
    signal_conflict: bool           # 维度间是否存在显著冲突
    conflict_description: str       # 冲突描述
    data_coverage: str              # insufficient | low | medium | high
    event_type: Optional[str] = None       # 自动推断或手动指定的事件类型
    weights_used: dict = field(default_factory=dict)  # 实际使用的维度权重


# ── 事件类型自适应权重 ─────────────────────────────────────────────
#
# 不同事件类型中，各维度的重要性并不相同：
# - 宏观政策事件 → 事件本身权重最高，观点和技术面辅助
# - 财报/行业数据 → 观点和事件并重，资金补充
# - 加密情绪事件 → 资金流和市场结构权重最高
# - 无特定事件 → 默认均衡权重

_DEFAULT_WEIGHTS = {
    "claim": 0.30,
    "market": 0.25,
    "flow": 0.20,
    "event": 0.25,
}

_EVENT_TYPE_WEIGHTS = {
    "macro_policy": {
        "claim": 0.20, "market": 0.15, "flow": 0.15, "event": 0.50,
    },
    "central_bank": {
        "claim": 0.20, "market": 0.15, "flow": 0.15, "event": 0.50,
    },
    "earnings": {
        "claim": 0.30, "market": 0.20, "flow": 0.15, "event": 0.35,
    },
    "crypto_sentiment": {
        "claim": 0.15, "market": 0.25, "flow": 0.40, "event": 0.20,
    },
    "crypto_onchain": {
        "claim": 0.15, "market": 0.20, "flow": 0.45, "event": 0.20,
    },
    "technical_breakout": {
        "claim": 0.15, "market": 0.50, "flow": 0.20, "event": 0.15,
    },
    "geopolitical": {
        "claim": 0.25, "market": 0.15, "flow": 0.15, "event": 0.45,
    },
    "regulatory": {
        "claim": 0.20, "market": 0.15, "flow": 0.20, "event": 0.45,
    },
}


def _get_weights(event_type: Optional[str] = None) -> dict:
    """根据事件类型返回自适应权重。"""
    if event_type and event_type in _EVENT_TYPE_WEIGHTS:
        return _EVENT_TYPE_WEIGHTS[event_type]
    return _DEFAULT_WEIGHTS


def score_claims(claims: List[dict]) -> DimensionScore:
    """对私域观点进行加权情感评分。

    公式：score = Σ(stance_val × trust × confidence) / Σ(trust × confidence)
    """
    if not claims:
        return DimensionScore("claim", 0.0, 0.0, 0, "无私域观点数据")

    stance_map = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "conditional": 0.0}
    weighted_sum = 0.0
    weight_total = 0.0
    contradicted_count = 0

    for c in claims:
        stance_val = stance_map.get(c.get("stance", "neutral"), 0.0)
        trust = float(c.get("trust_weight", 0.5))
        conf = float(c.get("confidence", 0.5))
        is_contra = c.get("is_contradicted", False)

        if is_contra:
            contradicted_count += 1
            trust *= 0.3  # 被矛盾的观点大幅降权

        w = trust * conf
        weighted_sum += stance_val * w
        weight_total += w

    score = weighted_sum / weight_total if weight_total > 0 else 0.0
    score = max(-1.0, min(1.0, score))

    # 一致性：如果所有观点方向一致，confidence 更高
    stances = [c.get("stance", "neutral") for c in claims if not c.get("is_contradicted")]
    unique_stances = set(s for s in stances if s in ("bullish", "bearish"))
    consistency = 1.0 if len(unique_stances) <= 1 else 0.5

    data_conf = min(1.0, len(claims) / 5) * consistency
    direction = "看多" if score > 0.15 else ("看空" if score < -0.15 else "中性")

    return DimensionScore(
        dimension="claim",
        score=round(score, 3),
        confidence=round(data_conf, 2),
        data_points=len(claims),
        summary=f"私域观点{len(claims)}条，加权方向{direction}（{score:+.2f}），{contradicted_count}条被矛盾",
        details={
            "contradicted_count": contradicted_count,
            "consistency": consistency,
            "stance_distribution": {s: stances.count(s) for s in set(stances)},
        },
    )


def score_market(market: Optional[dict]) -> DimensionScore:
    """对技术面信号进行综合评分。

    综合 RSI + 趋势 + 波动性 + 均线位置。
    """
    if not market or not market.get("price"):
        return DimensionScore("market", 0.0, 0.0, 0, "无市场数据")

    sub_scores = []
    details = {}

    # RSI 评分：超买超卖
    rsi = market.get("rsi_14")
    if rsi is not None:
        if rsi > 70:
            rsi_score = -0.3 - (rsi - 70) / 60  # 超买偏空
        elif rsi < 30:
            rsi_score = 0.3 + (30 - rsi) / 60   # 超卖偏多
        else:
            rsi_score = (rsi - 50) / 40          # 线性映射
        sub_scores.append(("rsi", rsi_score, 0.3))
        details["rsi_score"] = round(rsi_score, 3)

    # 趋势评分
    trend = market.get("trend_direction", "sideways")
    trend_map = {"uptrend": 0.5, "downtrend": -0.5, "sideways": 0.0}
    trend_score = trend_map.get(trend, 0.0)
    sub_scores.append(("trend", trend_score, 0.35))
    details["trend_score"] = trend_score

    # 价格 vs MA 评分
    price = market.get("price")
    ma20 = market.get("ma_20")
    ma50 = market.get("ma_50")
    if price and ma20 and ma50:
        above_ma20 = 0.3 if price > ma20 else -0.3
        above_ma50 = 0.3 if price > ma50 else -0.3
        ma_score = (above_ma20 + above_ma50) / 2
        sub_scores.append(("ma_position", ma_score, 0.2))
        details["ma_score"] = round(ma_score, 3)

    # 波动性评分：高波动 = 风险高 = 略偏空
    vol = market.get("volatility_state", "normal")
    vol_map = {"high": -0.2, "normal": 0.0, "low": 0.1}
    vol_score = vol_map.get(vol, 0.0)
    sub_scores.append(("volatility", vol_score, 0.15))
    details["vol_score"] = vol_score

    # 加权综合
    if sub_scores:
        total_w = sum(w for _, _, w in sub_scores)
        score = sum(s * w for _, s, w in sub_scores) / total_w if total_w > 0 else 0.0
    else:
        score = 0.0

    score = max(-1.0, min(1.0, score))
    data_conf = min(1.0, len(sub_scores) / 4)
    direction = "偏多" if score > 0.1 else ("偏空" if score < -0.1 else "中性")

    return DimensionScore(
        dimension="market",
        score=round(score, 3),
        confidence=round(data_conf, 2),
        data_points=len(sub_scores),
        summary=f"技术面{direction}（{score:+.2f}），RSI={rsi}，趋势={trend}",
        details=details,
    )


def score_flow(flow_signals: List[dict]) -> DimensionScore:
    """对资金流信号进行极端度评分。

    资金费率过高 → 情绪过热 → 偏空（反转预期）
    """
    if not flow_signals:
        return DimensionScore("flow", 0.0, 0.0, 0, "无资金信号数据")

    direction_map = {"bullish": 0.4, "bearish": -0.4, "neutral": 0.0}
    scores = []

    for sig in flow_signals:
        d = sig.get("direction", "neutral")
        base = direction_map.get(d, 0.0)

        # 如果描述中包含"过热"/"极端"关键词，反转信号
        desc = sig.get("description", "")
        if any(kw in desc for kw in ("过热", "极端", "极高", "overheated")):
            base *= -0.5  # 情绪过热时反转

        scores.append(base)

    score = sum(scores) / len(scores) if scores else 0.0
    score = max(-1.0, min(1.0, score))
    data_conf = min(1.0, len(flow_signals) / 3)
    direction = "偏多" if score > 0.1 else ("偏空" if score < -0.1 else "中性")

    return DimensionScore(
        dimension="flow",
        score=round(score, 3),
        confidence=round(data_conf, 2),
        data_points=len(flow_signals),
        summary=f"资金信号{len(flow_signals)}条，方向{direction}（{score:+.2f}）",
    )


def score_events(events: List[dict]) -> DimensionScore:
    """对事件信号进行加权影响力评分。

    按重要性加权，重要性 > 0.7 的事件权重更高。
    """
    if not events:
        return DimensionScore("event", 0.0, 0.0, 0, "无事件数据")

    direction_map = {"positive": 0.5, "negative": -0.5, "neutral": 0.0,
                     "bullish": 0.5, "bearish": -0.5}
    weighted_sum = 0.0
    weight_total = 0.0

    for ev in events:
        imp = float(ev.get("importance", 0.5))
        d = ev.get("impact_direction", ev.get("sentiment", "neutral"))
        base = direction_map.get(d, 0.0)
        w = imp ** 1.5  # 高重要性非线性加权
        weighted_sum += base * w
        weight_total += w

    score = weighted_sum / weight_total if weight_total > 0 else 0.0
    score = max(-1.0, min(1.0, score))
    data_conf = min(1.0, len(events) / 4)
    direction = "利好" if score > 0.1 else ("利空" if score < -0.1 else "中性")

    return DimensionScore(
        dimension="event",
        score=round(score, 3),
        confidence=round(data_conf, 2),
        data_points=len(events),
        summary=f"事件{len(events)}条，加权影响{direction}（{score:+.2f}）",
    )


def _detect_event_type(events: List[dict]) -> Optional[str]:
    """从事件列表中推断主要事件类型。
    
    匹配规则：取重要性最高的事件，检查其 category / type / title 关键词。
    """
    if not events:
        return None
    
    sorted_events = sorted(events, key=lambda e: float(e.get("importance", 0.5)), reverse=True)
    top = sorted_events[0]
    
    cat = (top.get("category") or top.get("type") or "").lower()
    title = (top.get("title") or "").lower()
    
    keyword_map = {
        "macro_policy": ["fomc", "财政", "fiscal", "gdp", "cpi", "ppi", "inflation", "通胀"],
        "central_bank": ["央行", "fed", "boj", "ecb", "加息", "降息", "rate", "qe", "qt"],
        "earnings": ["财报", "earnings", "revenue", "净利润", "eps"],
        "crypto_sentiment": ["情绪", "sentiment", "fomo", "panic", "fear", "greed"],
        "crypto_onchain": ["onchain", "链上", "whale", "巨鲸", "exchange flow"],
        "technical_breakout": ["突破", "breakout", "跌破", "breakdown", "golden cross"],
        "geopolitical": ["战争", "war", "制裁", "sanction", "关税", "tariff"],
        "regulatory": ["监管", "regulation", "sec", "审查", "ban", "禁令"],
    }
    
    if cat in _EVENT_TYPE_WEIGHTS:
        return cat
    
    text = f"{cat} {title}"
    for etype, keywords in keyword_map.items():
        if any(kw in text for kw in keywords):
            return etype
    
    return None


def compute_scorecard(
    asset: str,
    claims: List[dict],
    market: Optional[dict],
    flow_signals: List[dict],
    events: List[dict],
    event_type: Optional[str] = None,
) -> SignalScorecard:
    """计算完整的量化信号评分卡。
    
    event_type: 可手动指定事件类型，不指定则自动推断。
    """
    dims = [
        score_claims(claims),
        score_market(market),
        score_flow(flow_signals),
        score_events(events),
    ]

    # 自适应权重
    resolved_type = event_type or _detect_event_type(events)
    weights = _get_weights(resolved_type)

    # 加权综合得分
    total_weight = 0.0
    weighted_sum = 0.0
    for d in dims:
        w = weights.get(d.dimension, 0.25) * d.confidence
        weighted_sum += d.score * w
        total_weight += w

    composite = weighted_sum / total_weight if total_weight > 0 else 0.0
    composite = max(-1.0, min(1.0, composite))

    # 综合置信度
    active_dims = [d for d in dims if d.data_points > 0]
    composite_conf = sum(d.confidence for d in active_dims) / max(len(active_dims), 1)

    # 冲突检测：如果有维度得分符号相反且幅度都 > 0.2，标记冲突
    pos_dims = [d for d in dims if d.score > 0.2 and d.data_points > 0]
    neg_dims = [d for d in dims if d.score < -0.2 and d.data_points > 0]
    has_conflict = bool(pos_dims) and bool(neg_dims)

    conflict_desc = ""
    if has_conflict:
        pos_names = [d.dimension for d in pos_dims]
        neg_names = [d.dimension for d in neg_dims]
        conflict_desc = f"信号冲突：{'+'.join(pos_names)} 偏多 vs {'+'.join(neg_names)} 偏空"

    # 数据覆盖等级
    total_points = sum(d.data_points for d in dims)
    if total_points == 0:
        coverage = "insufficient"
    elif len(active_dims) == 1:
        coverage = "low"
    elif len(active_dims) <= 2:
        coverage = "medium"
    else:
        coverage = "high"

    return SignalScorecard(
        asset=asset,
        dimensions=dims,
        composite_score=round(composite, 3),
        composite_confidence=round(composite_conf, 2),
        signal_conflict=has_conflict,
        conflict_description=conflict_desc,
        data_coverage=coverage,
        event_type=resolved_type,
        weights_used=weights,
    )


def scorecard_to_prompt_section(sc: SignalScorecard) -> str:
    """将评分卡转为 prompt 文本段落，供 LLM 参考。"""
    lines = [
        "\n【量化信号预评分（规则引擎计算，非 AI 生成）】",
        f"综合得分：{sc.composite_score:+.3f}（-1空 ~ +1多），置信度：{sc.composite_confidence:.0%}",
        f"数据覆盖：{sc.data_coverage}",
    ]
    if sc.event_type:
        lines.append(f"事件类型：{sc.event_type}（权重已自适应调整）")
    if sc.signal_conflict:
        lines.append(f"⚠️ {sc.conflict_description}")
    lines.append("")
    for d in sc.dimensions:
        if d.data_points > 0:
            w = sc.weights_used.get(d.dimension, 0.25)
            lines.append(f"  [{d.dimension}] {d.score:+.3f}（置信{d.confidence:.0%}，权重{w:.0%}）— {d.summary}")
    lines.append("\n请将以上量化评分作为参考，结合原始数据给出你的综合判断。")
    lines.append("如果你的判断与量化评分方向不一致，请在叙述中说明原因。")
    return "\n".join(lines)
