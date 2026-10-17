"""G18 Agent-Specific Retrieval (Anthropic Multi-Agent Mode)

参考 Anthropic Multi-Agent Research System (2025-06):
> "Subagents facilitate compression by operating in parallel with their own
>  context windows, exploring different aspects of the question simultaneously."
>
> "Each subagent provides separation of concerns—distinct tools, prompts,
>  and exploration trajectories—which reduces path dependency."
>
> "multi-agent system outperformed single-agent by 90.2% on internal eval"

设计：
  - shared base context（_gather_context）：所有角色共享的 claims/events/market
  - per-agent context（本服务）：每个角色用专属 query 额外深挖一次 retrieval
  - 在 committee_workflow 里把两类合并后传给 debate_round

不破坏可重现性：
  - DecisionSnapshot 仍冻结 base claims（核心证据）
  - per-agent claims 作为辅助输入流过角色 prompt（如果未来需要持久化，可扩展 schema）

老路径完全兼容：
  - 若 enable_agent_retrieval=False（默认）则直接返回空 dict
  - 若服务降级（query_for_briefing 失败）也返回空 dict，不影响主流程
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.utils.source_scope import clean_source_ids


# ── 角色专属查询模板 ─────────────────────────────────────────────
# 每个 role 通过附加"视角关键词"扩展原 query，触达不同证据子空间。
# 不是为了"分裂事实基础"，而是为了"补足共享 retrieval 可能漏掉的角度"。
#
# 例如：question = "BTC 是否能突破前高"
#   → baseline: "BTC 是否能突破前高 基本面 共识 中性分析"
#   → risk    : "BTC 是否能突破前高 风险 看空 利空 回撤 反例"
#   → audit   : "BTC 是否能突破前高 来源可信 反驳 矛盾 证伪"
#
# 这样 risk 角色除了拿到 base context，还会额外召回"看空证据/历史回撤"，
# 让"风险"视角不被一份偏中性的共享 context 限制住。

ROLE_LENS_KEYWORDS: dict[str, list[str]] = {
    "baseline": [
        "基本面", "共识", "中性分析", "主流观点", "fundamental", "consensus",
    ],
    "probability": [
        "历史相似", "形态", "概率分布", "胜率", "频率", "回测", "历史回测",
        "historical similarity", "base rate", "probability",
    ],
    "risk": [
        "风险", "看空", "利空", "回撤", "下行", "反例", "崩盘", "黑天鹅",
        "drawdown", "downside", "tail risk", "bearish",
    ],
    "structure": [
        "技术面", "资金流", "仓位结构", "持仓", "多空比", "未平仓",
        "成交量", "RSI", "MACD", "K 线", "均线",
        "technical", "flow", "positioning",
    ],
    "audit": [
        "来源可信", "反驳", "矛盾", "证伪", "可疑", "被打脸", "数据真实性",
        "credibility", "contradict", "debunk",
    ],
    "pnl": [
        "执行成本", "滑点", "流动性", "深度", "点差", "手续费", "下单成本",
        "execution cost", "slippage", "liquidity",
    ],
}


def build_role_query(base_question: str, role: str) -> str:
    """组合 base question + role lens 关键词。

    - 不重复添加已存在的关键词
    - 默认拼接 3 个 lens 关键词，避免 query 过长
    - 未识别 role 时退化为原 query
    """
    base = (base_question or "").strip()
    lens = ROLE_LENS_KEYWORDS.get(role, [])
    if not lens or not base:
        return base
    # 取前 3 个未在 base 中出现的 lens 关键词
    extras: list[str] = []
    base_lower = base.lower()
    for kw in lens:
        if kw.lower() in base_lower:
            continue
        extras.append(kw)
        if len(extras) >= 3:
            break
    if not extras:
        return base
    return f"{base} {' '.join(extras)}"


def gather_for_role(
    db: Session,
    role: str,
    base_question: str,
    asset: str,
    limit: int = 5,
    base_claim_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """为某个角色召回额外的 claims。

    Args:
        db:              SQLAlchemy session（沿用主 context 的 session）
        role:            committee seat id (baseline/probability/risk/structure/audit/pnl)
        base_question:   原始用户问题
        asset:           主资产代码
        limit:           最多返回多少条
        base_claim_ids:  已经在 base context 里的 claim id 列表，去重避免重复
        source_ids:      用户显式限定的资料源；存在时必须作为硬约束传给 RAG

    Returns:
        list of dict（与 query_for_briefing 输出 schema 一致），可能为 []。
        失败时静默降级为 [] 不抛异常。
    """
    if not role or not base_question or not base_question.strip():
        return []

    role_query = build_role_query(base_question, role)
    if role_query == base_question.strip():
        # role 未识别 → 直接复用 base 召回，不再额外查（避免重复 cost）
        return []
    clean_ids = clean_source_ids(source_ids)

    try:
        # 延迟 import 避免循环依赖
        from app.mcps.research_memory_mcp import ResearchMemoryMCP
        memory = ResearchMemoryMCP(db)
        resp = memory.query_for_briefing(
            query=role_query,
            asset=asset,
            limit=limit,
            source_ids=clean_ids,
        )
        raw = (resp.data if resp else None) or []
    except Exception as e:
        logger.warning(f"[AgentRetrieval] role={role} query failed: {e}")
        return []

    # 规范化（与 _gather_context 对齐）
    base_set = set(base_claim_ids or [])
    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        cid = c.get("id", "")
        # 跳过已在 base context 里的（避免重复）
        if cid and cid in base_set:
            continue
        out.append({
            "id": cid,
            "summary": c.get("summary", ""),
            "stance": c.get("stance", "neutral"),
            "confidence": c.get("confidence", 0.5),
            "trust_weight": c.get("trust_weight", 0.5),
            "time_horizon": c.get("time_horizon"),
            "is_contradicted": c.get("is_contradicted", False),
            "_role_lens": role,  # 标记是哪个 role 召回的
        })
    return out


def gather_for_all_roles(
    db: Session,
    active_seats: list[str],
    base_question: str,
    asset: str,
    limit_per_role: int = 5,
    base_claims: list[dict] | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """批量为所有 active seats 召回 role-specific claims。

    Returns:
        dict mapping role → list of agent_specific claims
        没有 base_question 或全部召回为空时返回 {}。
    """
    if not base_question or not base_question.strip():
        return {}
    clean_ids = clean_source_ids(source_ids)

    base_ids: list[str] = []
    if base_claims:
        for c in base_claims:
            if isinstance(c, dict) and c.get("id"):
                base_ids.append(c["id"])

    out: dict[str, list[dict[str, Any]]] = {}
    for seat in active_seats:
        if seat not in ROLE_LENS_KEYWORDS:
            continue
        items = gather_for_role(
            db,
            role=seat,
            base_question=base_question,
            asset=asset,
            limit=limit_per_role,
            base_claim_ids=base_ids,
            source_ids=clean_ids,
        )
        if items:
            out[seat] = items
    return out


__all__ = [
    "ROLE_LENS_KEYWORDS",
    "build_role_query",
    "gather_for_role",
    "gather_for_all_roles",
]
