"""Claim Quality Service — 观点质量评分 + 语义合并

两大功能：
1. **质量评分**：对每条 Claim 计算结构化质量分数（0-1）
2. **语义合并**：检测与已有 Claim 的语义重复，支持合并补全

评分哲学（宽容策略）：
- 仅对明确的逻辑错误、信息过时或自相矛盾的观点给低分
- 缺少可选字段（如 invalidation_conditions）不会大幅降低评分
- 降级为 D 仅限于：无任何实质内容、明确逻辑矛盾、或来源极不可信
- 大多数观点应获得 B 或以上评级

设计原则：
- 纯计算逻辑，不调用 LLM（零成本）
- 不写数据库，只返回评估结果
- 供 ingestion_workflow 在持久化前调用
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ClaimQualityScore:
    """单条 Claim 的质量评分"""
    completeness: float     # 信息完整度 0-1
    verifiability: float    # 可验证性 0-1
    source_trust: float     # 来源可信度 0-1
    quote_support: float    # 原文支撑 0-1
    overall: float          # 综合得分 0-1
    grade: str              # A/B/C/D 等级
    issues: List[str]       # 质量问题列表


@dataclass
class DedupResult:
    """去重检测结果"""
    is_duplicate: bool
    similar_claim_id: Optional[str]
    similarity: float       # 0-1
    recommendation: str     # "keep" | "merge" | "skip"
    reason: str


# ── 质量评分 ─────────────────────────────────────────────────

def score_claim_quality(claim: dict) -> ClaimQualityScore:
    """计算单条 Claim 的质量分数。

    Args:
        claim: dict 包含 summary, claim_type, stance, time_horizon,
               asset_scope, theme_scope, confidence, reasoning_basis,
               conditions, invalidation_conditions, full_quote, trust_weight 等字段
    """
    issues = []

    # 1. 信息完整度 (0-1)
    # 必要字段（权重高）和可选字段（权重低）分开计算
    required_checks = {
        "summary": bool(claim.get("summary", "").strip()),
        "stance": claim.get("stance") in ("bullish", "bearish", "neutral", "conditional"),
        "asset_scope": bool(claim.get("asset_scope")),
    }
    optional_checks = {
        "claim_type": bool(claim.get("claim_type")),
        "time_horizon": claim.get("time_horizon", "unspecified") != "unspecified",
        "reasoning_basis": bool(claim.get("reasoning_basis")),
        "conditions": bool(claim.get("conditions")),
        "invalidation_conditions": bool(claim.get("invalidation_conditions")),
    }
    required_score = sum(1 for v in required_checks.values() if v) / max(len(required_checks), 1)
    optional_score = sum(1 for v in optional_checks.values() if v) / max(len(optional_checks), 1)
    completeness = required_score * 0.7 + optional_score * 0.3

    if not required_checks["summary"]:
        issues.append("缺少观点摘要(summary)，严重问题")
    if not optional_checks["reasoning_basis"]:
        issues.append("缺少推理依据(reasoning_basis)，建议补充")

    # 2. 可验证性 (0-1)
    verifiability = 0.0
    # 有明确资产
    if claim.get("asset_scope"):
        verifiability += 0.3
    # 有明确时间范围
    if claim.get("time_horizon", "unspecified") != "unspecified":
        verifiability += 0.2
    # 有明确立场（非 neutral/conditional）
    if claim.get("stance") in ("bullish", "bearish"):
        verifiability += 0.2
    # 有失效条件
    if claim.get("invalidation_conditions"):
        verifiability += 0.2
    # 高置信度
    conf = float(claim.get("confidence", 0.5))
    if conf >= 0.7:
        verifiability += 0.1

    # 3. 来源可信度
    source_trust = float(claim.get("trust_weight", 0.5))

    # 4. 原文支撑
    quote = claim.get("full_quote", "")
    if quote and len(quote) > 20:
        quote_support = 1.0
    elif quote:
        quote_support = 0.5
    else:
        quote_support = 0.0
        issues.append("无原文引用支撑")

    # 综合得分（加权，压低可选字段的影响）
    overall = (
        completeness * 0.30 +
        verifiability * 0.25 +
        source_trust * 0.25 +
        quote_support * 0.20
    )

    # 等级（宽容策略：仅明确问题才给 D）
    # D 条件：无实质内容 OR 来源极不可信
    has_fatal_issue = (
        not required_checks["summary"]  # 无摘要
        or (source_trust < 0.15)         # 来源极不可信
    )
    if overall >= 0.75:
        grade = "A"
    elif overall >= 0.50:
        grade = "B"
    elif overall >= 0.30 and not has_fatal_issue:
        grade = "C"
    else:
        grade = "D"
        issues.append("存在明确质量问题（无实质内容或来源极不可信），建议人工复核")

    return ClaimQualityScore(
        completeness=round(completeness, 3),
        verifiability=round(verifiability, 3),
        source_trust=round(source_trust, 3),
        quote_support=round(quote_support, 3),
        overall=round(overall, 3),
        grade=grade,
        issues=issues,
    )


# ── 语义去重 ─────────────────────────────────────────────────

_DEDUP_SIMILARITY_THRESHOLD = 0.92  # 余弦相似度 >= 此值视为重复


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _asset_overlap(scope_a: List[str], scope_b: List[str]) -> float:
    """计算两个资产范围的重叠度 (Jaccard)"""
    if not scope_a or not scope_b:
        return 0.0
    set_a = set(s.upper() for s in scope_a)
    set_b = set(s.upper() for s in scope_b)
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def check_duplicate(
    new_claim: dict,
    new_embedding: Optional[List[float]],
    existing_claims: List[dict],
    existing_embeddings: List[Optional[List[float]]],
) -> DedupResult:
    """检测新 Claim 是否与已有 Claims 重复。

    Args:
        new_claim: 待检测的新 claim dict
        new_embedding: 新 claim 的 embedding 向量（可能为 None）
        existing_claims: 已存在的 claim dict 列表
        existing_embeddings: 对应的 embedding 向量列表

    Returns:
        DedupResult 包含去重建议
    """
    if not existing_claims:
        return DedupResult(False, None, 0.0, "keep", "无已有观点，直接保留")

    best_sim = 0.0
    best_idx = -1
    best_claim_id = None

    for i, (ec, ee) in enumerate(zip(existing_claims, existing_embeddings)):
        sim = 0.0

        # 语义相似度（embedding）
        if new_embedding and ee:
            sem_sim = _cosine_similarity(new_embedding, ee)
            sim = sem_sim * 0.7  # embedding 占 70%
        else:
            # fallback: 简单文本比较
            new_summary = (new_claim.get("summary") or "").strip()
            ex_summary = (ec.get("summary") or "").strip()
            if new_summary and ex_summary:
                # 简单 overlap
                new_words = set(new_summary)
                ex_words = set(ex_summary)
                char_overlap = len(new_words & ex_words) / max(len(new_words | ex_words), 1)
                sim = char_overlap * 0.5

        # 资产范围重叠
        asset_sim = _asset_overlap(
            new_claim.get("asset_scope", []),
            ec.get("asset_scope", []),
        )
        sim += asset_sim * 0.15

        # 立场一致性
        if new_claim.get("stance") == ec.get("stance"):
            sim += 0.15

        if sim > best_sim:
            best_sim = sim
            best_idx = i
            best_claim_id = ec.get("id")

    # 判断
    if best_sim >= _DEDUP_SIMILARITY_THRESHOLD:
        return DedupResult(
            is_duplicate=True,
            similar_claim_id=best_claim_id,
            similarity=round(best_sim, 3),
            recommendation="skip",
            reason=f"与已有观点高度重复（相似度{best_sim:.1%}），建议跳过",
        )
    elif best_sim >= 0.80:
        return DedupResult(
            is_duplicate=False,
            similar_claim_id=best_claim_id,
            similarity=round(best_sim, 3),
            recommendation="merge",
            reason=f"与已有观点部分重叠（相似度{best_sim:.1%}），建议合并更新",
        )
    else:
        return DedupResult(
            is_duplicate=False,
            similar_claim_id=None,
            similarity=round(best_sim, 3),
            recommendation="keep",
            reason="无显著重复，保留",
        )


def merge_claims(existing_claim: dict, new_claim: dict) -> dict:
    """将两个相似但不完全相同的 claim 合并补全。
    
    合并策略：
    - summary: 保留较长/较新的，或拼接差异部分
    - 列表字段（asset_scope, conditions 等）: 取并集
    - 数值字段（confidence, trust_weight）: 取较高值
    - 其他文本字段：优先使用已有值，新值补空
    
    Returns:
        合并后的 claim dict（基于 existing_claim，补全新增信息）
    """
    merged = dict(existing_claim)
    
    # summary 合并：如果新的更长或包含不同信息，追加补充
    old_summary = (existing_claim.get("summary") or "").strip()
    new_summary = (new_claim.get("summary") or "").strip()
    if new_summary and old_summary:
        # 检查新摘要是否包含旧摘要没有的实质内容
        old_chars = set(old_summary)
        new_chars = set(new_summary)
        novelty = len(new_chars - old_chars) / max(len(new_chars), 1)
        if novelty > 0.3 and len(new_summary) > 15:
            merged["summary"] = f"{old_summary}；补充：{new_summary}"
        elif len(new_summary) > len(old_summary) * 1.5:
            merged["summary"] = new_summary
    elif new_summary and not old_summary:
        merged["summary"] = new_summary
    
    # 列表字段：取并集（reasoning_basis 也是 List[str]）
    for list_field in ("asset_scope", "theme_scope", "conditions", "invalidation_conditions", "reasoning_basis"):
        old_list = existing_claim.get(list_field) or []
        new_list = new_claim.get(list_field) or []
        # 兼容旧数据可能是 str 的情况
        if isinstance(old_list, str):
            old_list = [old_list] if old_list.strip() else []
        if isinstance(new_list, str):
            new_list = [new_list] if new_list.strip() else []
        if new_list:
            # 去重合并
            seen = set()
            combined = []
            for item in old_list + new_list:
                item_str = str(item).strip().lower()
                if item_str not in seen:
                    seen.add(item_str)
                    combined.append(item)
            merged[list_field] = combined
    
    # 数值字段：取较高值
    for num_field in ("confidence",):
        old_val = float(existing_claim.get(num_field, 0) or 0)
        new_val = float(new_claim.get(num_field, 0) or 0)
        merged[num_field] = max(old_val, new_val)
    
    # trust_weight: 加权平均（新旧各半）
    old_tw = float(existing_claim.get("trust_weight", 0.5) or 0.5)
    new_tw = float(new_claim.get("trust_weight", 0.5) or 0.5)
    merged["trust_weight"] = round((old_tw + new_tw) / 2, 3)
    
    # 文本字段：已有值优先，新值补空
    for text_field in ("full_quote", "claim_type", "time_horizon"):
        old_val = existing_claim.get(text_field)
        new_val = new_claim.get(text_field)
        if not old_val and new_val:
            merged[text_field] = new_val
    
    # stance: 优先保留已有，除非新的更具体
    old_stance = existing_claim.get("stance", "neutral")
    new_stance = new_claim.get("stance", "neutral")
    if old_stance in ("neutral", "conditional") and new_stance in ("bullish", "bearish"):
        merged["stance"] = new_stance
    
    # 标记为合并结果
    merged["_merged"] = True
    merged["_merged_from"] = new_claim.get("id") or new_claim.get("summary", "")[:50]
    
    return merged


def batch_quality_check(claims: List[dict]) -> Tuple[List[dict], dict]:
    """批量质量评估，返回 (带质量标注的 claims, 统计摘要)。

    在每个 claim dict 中添加 _quality_score 和 _quality_grade 字段。
    """
    results = []
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    total_score = 0.0

    for c in claims:
        qs = score_claim_quality(c)
        c["_quality_score"] = qs.overall
        c["_quality_grade"] = qs.grade
        c["_quality_issues"] = qs.issues
        results.append(c)
        grade_counts[qs.grade] = grade_counts.get(qs.grade, 0) + 1
        total_score += qs.overall

    avg_score = total_score / len(claims) if claims else 0.0
    summary = {
        "total": len(claims),
        "avg_quality": round(avg_score, 3),
        "grade_distribution": grade_counts,
        "low_quality_count": grade_counts.get("D", 0),
    }
    return results, summary
