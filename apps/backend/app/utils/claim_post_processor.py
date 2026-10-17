"""B3: 统一的召回候选后处理管线

问题：chat_harness 的 reranker+stance-balance 只作用于 chat 模式；
briefing（query_for_briefing）和 debate 用的是另一条评分/筛选路径，
导致 A4、A5 两项改进的收益仅 chat 享受到。

本模块把"候选池 → 精排 → 融合打分 → 对立保留"这段与召回来源无关的
质量管线抽为单一函数 ``post_process_candidates``，三条路径都调用。

使用方仅负责"初级召回"：
    - chat: ``semantic_search.semantic_search(query, ...)``
    - briefing: 三路混合（semantic + asset_match + trust_boost）
    - debate: 同 briefing（复用 query_for_briefing）
然后把候选池传给本模块。

返回：
    PostProcessResult(
        claims=...,              # 最终 top-K
        stance_distribution=..., # 立场计数
        balanced_applied=...,    # 是否注入了对立观点
        reranker_applied=...,    # 是否启用了 reranker
        total_scanned=...,       # 候选池大小
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from loguru import logger

from app.schemas.claim_card import ClaimCard


# ══════════════════════════════════════════════════════════════════════════
# 立场归一化（兼容 bull/long/bearish/short/positive/negative 等多种写法）
# ══════════════════════════════════════════════════════════════════════════

def stance_of(claim: ClaimCard) -> str:
    s = (getattr(claim, "stance", "") or "").lower()
    if s in ("bullish", "bull", "long", "positive"):
        return "bullish"
    if s in ("bearish", "bear", "short", "negative"):
        return "bearish"
    if s in ("conditional", "conditional_bullish", "conditional_bearish"):
        return "conditional"
    return "neutral"


# ══════════════════════════════════════════════════════════════════════════
# 结果数据类
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class PostProcessResult:
    claims: List[ClaimCard] = field(default_factory=list)
    stance_distribution: Dict[str, int] = field(default_factory=dict)
    balanced_applied: bool = False
    reranker_applied: bool = False
    total_scanned: int = 0
    # G17: 自适应 top-K — 实际返回数 vs caller 期望数（top_k）
    # adaptive_applied=True 表示触发了 score 阈值过滤逻辑
    # final_count 是过滤后实际返回数（可能 != top_k）
    adaptive_applied: bool = False
    final_count: int = 0


# ══════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════

# B8: 默认融合权重（可被 grid search 覆盖）
DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {
    "sem": 0.50,
    "trust": 0.25,
    "conf": 0.12,
    "decay": 0.13,
}


def post_process_candidates(
    candidates: List[ClaimCard],
    query: str,
    *,
    top_k: int = 8,
    use_reranker: Optional[bool] = None,
    use_stance_balance: bool = True,
    trust_weight_fn: Optional[Callable[[ClaimCard], float]] = None,
    contradiction_penalty: float = 0.0,
    min_opposite_ratio: float = 0.2,
    dominant_threshold: float = 0.8,
    min_pool_opposite_ratio: float = 0.05,
    score_weights: Optional[Dict[str, float]] = None,
    context: str = "unknown",
) -> PostProcessResult:
    """候选池 → 精排 → 融合打分 → 对立保留 → top-K。

    Args:
        candidates: 初级召回出的候选 claims（可来自任意来源）
        query: 原始查询文本，用于 reranker
        top_k: 最终返回多少条
        use_reranker: None=读 env 默认；True/False 显式覆盖
        use_stance_balance: 是否启用 A5 对立保留
        trust_weight_fn: 自定义信任权重取值函数；None 时读 ``claim.source_trust_weight``
            （briefing 路径可传"读 SpeakerProfile 缓存"的闭包）
        contradiction_penalty: ``is_contradicted=True`` 时的分数惩罚（briefing 用 -0.3）
        min_opposite_ratio: 强制保留的对立观点占比下限
        dominant_threshold: 触发对立保留的"单边优势"阈值
        min_pool_opposite_ratio: 候选池对立占比阈值；低于此则不强行注入
        context: 日志标签
    """
    if not candidates:
        return PostProcessResult()

    scanned = len(candidates)

    # ── 1) Reranker（可选）────────────────────────────────────────
    reranker_scores: Dict[str, float] = {}
    reranker_applied = False
    if use_reranker is None:
        try:
            from app.config import settings
            use_reranker = bool(getattr(settings, "ENABLE_RERANKER", False))
        except Exception:
            use_reranker = False

    if use_reranker and scanned >= 2:
        try:
            from app.utils.reranker import get_reranker
            rr = get_reranker()
            if rr is not None:
                texts = [(c.summary or getattr(c, "full_quote", "") or "") for c in candidates]
                ranked = rr.rerank(query, texts)  # [(idx, score), ...] 降序
                reranker_scores = {candidates[i].id: s for i, s in ranked}
                reranker_applied = True
                logger.debug(f"[PostProcessor:{context}] reranker scored {len(ranked)}")
        except Exception as e:
            logger.warning(f"[PostProcessor:{context}] reranker failed: {e}")

    # ── 2) 融合打分 ───────────────────────────────────────────────
    from app.utils.time_decay import time_decay_factor

    def _trust(c: ClaimCard) -> float:
        if trust_weight_fn:
            try:
                return float(trust_weight_fn(c) or 0.5)
            except Exception:
                return 0.5
        return float(getattr(c, "source_trust_weight", 0.5) or 0.5)

    # B8: 应用权重（未传则用 DEFAULT_SCORE_WEIGHTS）
    weights = {**DEFAULT_SCORE_WEIGHTS, **(score_weights or {})}
    w_sem = weights["sem"]
    w_trust = weights["trust"]
    w_conf = weights["conf"]
    w_decay = weights["decay"]

    def _score(c: ClaimCard, idx: int) -> float:
        # 语义相关性：reranker 分数优先，否则用候选池内的归一排名
        if reranker_scores and c.id in reranker_scores:
            sem = float(reranker_scores[c.id])
        else:
            sem = 1.0 - (idx / max(scanned, 1))
        trust = _trust(c)
        conf = float(getattr(c, "confidence", 0.5) or 0.5)
        decay = time_decay_factor(
            created_at=getattr(c, "created_at", None),
            time_horizon=getattr(c, "time_horizon", None),
            time_sensitive=bool(getattr(c, "time_sensitive", True)),
        )
        penalty = contradiction_penalty if getattr(c, "is_contradicted", False) else 0.0
        return sem * w_sem + trust * w_trust + conf * w_conf + decay * w_decay + penalty

    scored = [(c, _score(c, idx)) for idx, c in enumerate(candidates)]
    scored.sort(key=lambda x: x[1], reverse=True)

    # ── 3) 取初始 top-K（G17：自适应或固定）─────────────────────
    adaptive_applied = False
    try:
        from app.config import settings as _settings
        _adapt = bool(getattr(_settings, "ADAPTIVE_TOPK_ENABLED", False))
    except Exception:
        _adapt = False

    if _adapt:
        # 自适应：score >= 阈值 才入选；clip 到 [min, max]
        try:
            min_score = float(getattr(_settings, "ADAPTIVE_TOPK_MIN_SCORE", 0.45))
            min_k = int(getattr(_settings, "ADAPTIVE_TOPK_MIN", 1))
            max_factor = float(getattr(_settings, "ADAPTIVE_TOPK_MAX_FACTOR", 1.5))
            hard_max = int(getattr(_settings, "ADAPTIVE_TOPK_HARD_MAX", 15))
        except Exception:
            min_score, min_k, max_factor, hard_max = 0.45, 1, 1.5, 15

        # 上限 = min(top_k * factor, HARD_MAX)；下限 = min(min_k, top_k)
        # 注意 top_k 在 clip 范围内（<= max_k 且 >= min_k），保证退化时还原成 top_k
        max_k = min(int(top_k * max_factor), hard_max)
        min_k = min(min_k, top_k)
        qualifying = [(c, s) for c, s in scored if s >= min_score]
        n_qualify = len(qualifying)

        if n_qualify < min_k:
            # 阈值过严 → 至少给 min_k 条（用未过滤 scored）
            primary = [c for c, _ in scored[:min_k]]
        elif n_qualify > max_k:
            # 阈值通过太多 → 截到 max_k
            primary = [c for c, _ in qualifying[:max_k]]
            adaptive_applied = True
        else:
            primary = [c for c, _ in qualifying]
            # 仅在与固定 top_k 行为不同时算"applied"
            adaptive_applied = (n_qualify != top_k)

        if adaptive_applied:
            logger.info(
                f"[PostProcessor:{context}] adaptive top-K: kept {len(primary)} "
                f"(target top_k={top_k}, qualifying≥{min_score}={n_qualify}, "
                f"clip=[{min_k},{max_k}])"
            )
    else:
        primary: List[ClaimCard] = [c for c, _ in scored[:top_k]]

    # ── 4) 立场分布 ───────────────────────────────────────────────
    def _count(claims: List[ClaimCard]) -> Dict[str, int]:
        dist = {"bullish": 0, "bearish": 0, "neutral": 0, "conditional": 0}
        for c in claims:
            dist[stance_of(c)] = dist.get(stance_of(c), 0) + 1
        return dist

    dist = _count(primary)
    balanced_applied = False

    # ── 5) 对立保留（A5）────────────────────────────────────────
    if use_stance_balance and len(primary) >= 3:
        pool_dist = _count([c for c, _ in scored])
        pool_total = sum(pool_dist.values()) or 1
        total_primary = sum(dist.values()) or 1

        for dominant in ("bullish", "bearish"):
            opposite = "bearish" if dominant == "bullish" else "bullish"
            if dist.get(dominant, 0) / total_primary > dominant_threshold:
                # 候选池对立占比不足 → 真共识，不纠偏（避免 D-C1 过度纠偏）
                pool_opposite_ratio = pool_dist.get(opposite, 0) / pool_total
                if pool_opposite_ratio < min_pool_opposite_ratio:
                    logger.info(
                        f"[PostProcessor:{context}] stance-balance skipped: pool consensus "
                        f"(dominant={dominant} {dist.get(dominant, 0)}/{total_primary}, "
                        f"pool_{opposite}={pool_opposite_ratio:.1%} < {min_pool_opposite_ratio:.1%})"
                    )
                    break
                # 从候选池里挑对立观点
                opposite_pool = [
                    c for c, _ in scored
                    if stance_of(c) == opposite and c not in primary
                ]
                if opposite_pool:
                    needed = max(1, int(round(top_k * min_opposite_ratio)))
                    injected = opposite_pool[:needed]
                    # 替换 primary 中评分最低的同向观点
                    primary_scores = {c.id: s for c, s in scored}
                    same_side_sorted_asc = sorted(
                        [c for c in primary if stance_of(c) == dominant],
                        key=lambda c: primary_scores.get(c.id, 0.0),
                    )
                    to_drop = same_side_sorted_asc[:len(injected)]
                    primary = [c for c in primary if c not in to_drop] + injected
                    balanced_applied = True
                    logger.info(
                        f"[PostProcessor:{context}] stance-balance applied: "
                        f"+{len(injected)} {opposite} (dominant={dominant}, scanned={scanned})"
                    )
                    dist = _count(primary)
                    break

    return PostProcessResult(
        claims=primary,
        stance_distribution=dist,
        balanced_applied=balanced_applied,
        reranker_applied=reranker_applied,
        total_scanned=scanned,
        adaptive_applied=adaptive_applied,
        final_count=len(primary),
    )


__all__ = [
    "post_process_candidates",
    "PostProcessResult",
    "stance_of",
]
