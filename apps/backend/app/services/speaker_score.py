"""G19 Speaker-Aware Reranking — 多维 Speaker 画像融合评分

参考 Anthropic / LangChain 业界做法：
  retrieval rank 之外，还要考虑"说话人本身的可信度多维特征"。
  比如：
    - 一个 hit_rate=0.8 的资深 macro 分析师讲 macro，应当被排在
      hit_rate=0.3 的纯情绪派之上，即使他们的 cosine 相似度接近。
    - 同样质量的两段话，logic_robustness 高的应当优先。
    - 高情绪话术（emotional_intensity > 0.7）应当略微降权，避免
      被"煽动性强但逻辑粗糙"的来源主导决策。

设计：
  把 SpeakerProfile 已有的多维字段（V2 升级时已经存了）做加权融合：
    base_trust       40%   LLM 标定的初始可信度
    hit_rate         30%   实际命中率（OutcomeRepository 自动维护）
    logic_robustness 15%   论证链稳健性
    domain_match     10%   是否在该 speaker 擅长的领域里
    contrarian_value  5%   反向指标价值（即使不准也有信息量）
  +/- 情绪惩罚：emotional_intensity > 0.7 时 −0.05

向后兼容：
  - speaker = None              → 返回 0.5（中性默认，与老逻辑一致）
  - hit_rate / logic_robustness 任一为 NULL → 该项权重转给 base_trust
  - query_domains = None        → 跳过 domain_match，权重重分配
"""
from __future__ import annotations

from typing import Any, Optional


# 默认权重（合计 1.0；缺字段时会重分配）
DEFAULT_WEIGHTS = {
    "base_trust": 0.40,
    "hit_rate": 0.30,
    "logic_robustness": 0.15,
    "domain_match": 0.10,
    "contrarian_value": 0.05,
}

# 情绪强度惩罚阈值与幅度
EMOTIONAL_PENALTY_THRESHOLD = 0.7
EMOTIONAL_PENALTY = -0.05

# Speaker 缺失或全部字段都为 NULL 时的中性默认分
NEUTRAL_DEFAULT = 0.5


def _clamp01(x: float) -> float:
    """Clamp 到 [0, 1]。"""
    if x < 0:
        return 0.0
    if x > 1:
        return 1.0
    return x


def _compute_domain_match(speaker: Any, query_domains: Optional[list[str]]) -> Optional[float]:
    """计算 query 与 speaker 擅长领域的匹配度。

    Returns:
        - 0.0 ~ 1.0 表示匹配比例
        - None 表示无法计算（query_domains 未传 或 speaker 无领域信息）
    """
    if not query_domains:
        return None
    domains = list(getattr(speaker, "applicable_domains", []) or [])
    domains.extend(list(getattr(speaker, "domain_tags", []) or []))
    if not domains:
        return None
    speaker_domains_lower = {d.lower() for d in domains}
    hits = sum(1 for q in query_domains if q.lower() in speaker_domains_lower)
    return hits / len(query_domains) if query_domains else None


def compute_speaker_score(
    speaker: Any,
    query_domains: Optional[list[str]] = None,
    weights: Optional[dict[str, float]] = None,
) -> float:
    """计算 speaker 多维融合分数 ∈ [0, 1]。

    Args:
        speaker:        SpeakerProfile ORM 对象，或任何含同名属性的 duck-typed 对象。
                        None 时返回 NEUTRAL_DEFAULT (0.5)。
        query_domains:  当前 query 涉及的领域标签（如 ["macro", "crypto"]）。
                        None 时跳过 domain_match 维度，其权重重分配给 base_trust。
        weights:        自定义权重；缺省用 DEFAULT_WEIGHTS。

    Returns:
        0.0 ~ 1.0 之间的多维加权融合分数。
    """
    if speaker is None:
        return NEUTRAL_DEFAULT

    w = dict(weights or DEFAULT_WEIGHTS)

    # 收集每个维度的分数（None 表示该字段缺失）
    base_trust = getattr(speaker, "trust_weight", None)
    hit_rate = getattr(speaker, "hit_rate", None)
    logic_robustness = getattr(speaker, "logic_robustness", None)
    contrarian_value = getattr(speaker, "contrarian_value", None)
    emotional_intensity = getattr(speaker, "emotional_intensity", None)

    domain_match = _compute_domain_match(speaker, query_domains)

    # 收集"已有数据"的维度，缺失项的权重重分配给 base_trust
    components: dict[str, tuple[float, float]] = {}  # key → (value, weight)
    if base_trust is not None:
        components["base_trust"] = (float(base_trust), w["base_trust"])
    if hit_rate is not None:
        components["hit_rate"] = (float(hit_rate), w["hit_rate"])
    if logic_robustness is not None:
        components["logic_robustness"] = (float(logic_robustness), w["logic_robustness"])
    if domain_match is not None:
        components["domain_match"] = (float(domain_match), w["domain_match"])
    if contrarian_value is not None:
        components["contrarian_value"] = (float(contrarian_value), w["contrarian_value"])

    # 全字段缺失 → 退化为中性默认
    if not components:
        return NEUTRAL_DEFAULT

    # 重分配缺失维度的权重 → 全部归并到 base_trust（如果有）
    total_w_present = sum(weight for _, weight in components.values())
    missing_w = 1.0 - total_w_present
    if missing_w > 1e-6:
        if "base_trust" in components:
            v, weight = components["base_trust"]
            components["base_trust"] = (v, weight + missing_w)
        else:
            # 连 base_trust 也没有 → 把缺失权重等比分摊到现有维度
            scale = 1.0 / total_w_present if total_w_present > 0 else 0
            components = {k: (v, weight * scale) for k, (v, weight) in components.items()}

    score = sum(v * weight for v, weight in components.values())

    # 情绪强度惩罚（只有在数值 > 阈值时生效）
    if emotional_intensity is not None and float(emotional_intensity) > EMOTIONAL_PENALTY_THRESHOLD:
        score += EMOTIONAL_PENALTY

    return _clamp01(score)


def make_speaker_score_fn(
    speaker_cache: dict[str, Any],
    query_domains: Optional[list[str]] = None,
):
    """构建一个 trust_weight_fn 闭包，供 post_process_candidates 用。

    Args:
        speaker_cache:  {speaker_id: SpeakerProfile} 已经从 DB 拉好的字典
        query_domains:  query 涉及的领域

    Returns:
        Callable[[ClaimCard], float] — 输入 claim，返回 speaker_score
    """
    def _fn(claim: Any) -> float:
        sid = getattr(claim, "speaker_id", None)
        if not sid:
            return NEUTRAL_DEFAULT
        sp = speaker_cache.get(sid)
        return compute_speaker_score(sp, query_domains=query_domains)
    return _fn


__all__ = [
    "DEFAULT_WEIGHTS",
    "EMOTIONAL_PENALTY_THRESHOLD",
    "EMOTIONAL_PENALTY",
    "NEUTRAL_DEFAULT",
    "compute_speaker_score",
    "make_speaker_score_fn",
]
