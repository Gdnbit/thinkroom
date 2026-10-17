"""Chat Harness — LLM 环境工程层

职责：把一次裸 LLM.complete() 升级成受控、可追溯、立场平衡的对话。

职能分层：
    1. StanceBalancedRetriever  —— 召回时强制保留多空对立观点
    2. ContextPackBuilder        —— 把 claim/market 组装成分层结构化 context
    3. CitationRegistry          —— 为每条 claim 分配稳定 [N] 编号，便于前端跳转
    4. PromptTemplate            —— 带归因 + refusal 规则的 system prompt

被 chat.py 调用；后续 briefing / debate 也可复用。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union
import re
from sqlalchemy.orm import Session

from app.schemas.claim_card import ClaimCard
from app.services import semantic_search as sem_search
from app.utils.source_scope import clean_source_ids

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 数据类
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Citation:
    """单条引用，对应 context 中的 [N] 编号"""
    index: int              # 1-based
    claim_id: str
    stance: str             # bullish | bearish | neutral | conditional
    summary: str
    short_quote: str        # 截断后的原文引用
    trust_weight: float
    confidence: float
    time_horizon: Optional[str]
    speaker_id: Optional[str]
    geographic_scope: List[str] = field(default_factory=list)
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    source_id: Optional[str] = None
    evidence_span_id: Optional[str] = None
    span_type: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    page_or_timestamp: Optional[str] = None
    extraction_method: Optional[str] = None


@dataclass
class ChatContext:
    """Harness 产出的完整上下文，由 chat.py 注入到 LLM 调用"""
    context_block: str                          # 最终拼好的 context 文本（含 [N] 编号）
    citations: List[Citation] = field(default_factory=list)
    market_snapshot: Optional[Dict[str, Any]] = None   # 附加的行情快照（若识别到资产）
    stance_distribution: Dict[str, int] = field(default_factory=dict)  # bull/bear/neutral 计数
    total_candidates_scanned: int = 0
    balanced_applied: bool = False              # 是否因对立保留机制调整过结果
    search_tier: str = "unknown"                # pgvector|sqlite-vec|cosine|keyword
    # B1: token 预算诊断
    context_tokens: int = 0                     # context_block 估算 token 数
    context_dropped: int = 0                    # 因预算超限被丢弃的 claim 数
    # G15: parent-child retrieval 诊断
    parent_neighbors_added: int = 0             # 因 parent context 增加的 claim 条数
    quality_filters: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# 1. Stance-Balanced Retrieval
# ══════════════════════════════════════════════════════════════════════════

def _stance_of(claim: ClaimCard) -> str:
    s = (getattr(claim, "stance", "") or "").lower()
    if s in ("bullish", "bull", "long"):
        return "bullish"
    if s in ("bearish", "bear", "short"):
        return "bearish"
    if s in ("conditional",):
        return "conditional"
    return "neutral"


def _rerank_score(claim: ClaimCard, semantic_rank_idx: int, total: int) -> float:
    """融合打分：语义秩 + trust + confidence + 时间衰减（S4/G12）。

    sem_rank 归一到 0..1（越大越好）。时间衰减按 time_horizon 自适应半衰期，
    非时效敏感观点（理念/框架）不衰减。
    """
    from app.utils.time_decay import time_decay_factor
    sem_score = 1.0 - (semantic_rank_idx / max(total, 1))
    trust = float(getattr(claim, "source_trust_weight", 0.5) or 0.5)
    conf = float(getattr(claim, "confidence", 0.5) or 0.5)
    decay = time_decay_factor(
        created_at=getattr(claim, "created_at", None),
        time_horizon=getattr(claim, "time_horizon", None),
        time_sensitive=bool(getattr(claim, "time_sensitive", True)),
    )
    return sem_score * 0.50 + trust * 0.25 + conf * 0.12 + decay * 0.13


def balanced_semantic_search(
    db: Session,
    query: str,
    limit: int = 8,
    asset_filter: Optional[Union[str, List[str]]] = None,
    source_ids: Optional[List[str]] = None,
    exclude_expired: bool = True,
    exclude_contradicted: bool = True,
    candidate_multiplier: int = 5,          # 3→5: 扩大初级候选池提高 recall（grid search 实测 recall@8=79% 时留 21% 空间）
    min_opposite_ratio: float = 0.2,
    dominant_threshold: float = 0.8,
    min_pool_opposite_ratio: float = 0.05,
) -> tuple[List[ClaimCard], Dict[str, int], bool, int]:
    """立场平衡的语义搜索（chat 模式入口）。

    B3 重构：核心逻辑（reranker + 融合打分 + 对立保留）已迁移到
    ``app.utils.claim_post_processor.post_process_candidates``；
    本函数仅负责"chat 专属的初级召回"（semantic_search），再委托给公共管线。

    briefing / debate 使用各自的召回（三路混合）后，也可调用同一公共管线。

    Returns:
        (selected_claims, stance_counts, balanced_applied, scanned_total)
    """
    from app.utils.claim_post_processor import post_process_candidates

    # 1) chat 专属初级召回（语义搜索）
    candidates = sem_search.semantic_search(
        db=db,
        query=query,
        limit=max(limit * candidate_multiplier, 30),  # 底线从 16→30 以防 limit 过小导致池子太浅
        asset_filter=asset_filter,
        status_filter="active",
        source_ids=source_ids,
        exclude_expired=exclude_expired,
        exclude_contradicted=exclude_contradicted,
    )
    if not candidates:
        return [], {}, False, 0

    # 2) 公共后处理管线
    result = post_process_candidates(
        candidates=candidates,
        query=query,
        top_k=limit,
        use_reranker=None,  # 读 settings.ENABLE_RERANKER
        use_stance_balance=True,
        trust_weight_fn=None,  # chat 默认读 claim.source_trust_weight
        contradiction_penalty=0.0,  # chat 不对矛盾观点额外惩罚
        min_opposite_ratio=min_opposite_ratio,
        dominant_threshold=dominant_threshold,
        min_pool_opposite_ratio=min_pool_opposite_ratio,
        context="chat",
    )
    return (
        result.claims,
        result.stance_distribution,
        result.balanced_applied,
        result.total_scanned,
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. Context Pack Builder
# ══════════════════════════════════════════════════════════════════════════

def _claims_from_selected_sources(
    db: Session,
    source_ids: Optional[List[str]],
    limit: int,
    *,
    exclude_expired: bool = True,
    exclude_contradicted: bool = True,
) -> List[ClaimCard]:
    selected = clean_source_ids(source_ids)
    if not selected:
        return []
    try:
        from app.schemas.claim_card import ClaimRawSourceLink
        links = (
            db.query(ClaimRawSourceLink)
            .filter(ClaimRawSourceLink.source_id.in_(selected))
            .all()
        )
        claim_ids: list[str] = []
        seen = set()
        for link in links:
            if link.claim_id not in seen:
                claim_ids.append(link.claim_id)
                seen.add(link.claim_id)
        if not claim_ids:
            return []
        claims = (
            db.query(ClaimCard)
            .filter(ClaimCard.id.in_(claim_ids), ClaimCard.status == "active")
            .order_by(ClaimCard.created_at.desc())
            .limit(limit)
            .all()
        )
        now = datetime.now(timezone.utc)
        eligible: List[ClaimCard] = []
        for claim in claims:
            if exclude_contradicted and bool(getattr(claim, "is_contradicted", False)):
                continue
            valid_until = getattr(claim, "valid_until", None)
            if valid_until is not None and valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            if exclude_expired and valid_until is not None and valid_until < now:
                continue
            eligible.append(claim)
        return eligible
    except Exception as exc:
        logger.debug(f"[Harness] selected-source claim fallback skipped: {exc}")
        return []


def _small_selected_source_claims(
    db: Session,
    source_ids: Optional[List[str]],
    *,
    exclude_expired: bool,
    exclude_contradicted: bool,
) -> Optional[List[ClaimCard]]:
    """Return local claims for a small explicit scope, or ``None`` when semantic retrieval is warranted."""
    selected = clean_source_ids(source_ids)
    if not selected:
        return None
    try:
        from app.config import settings
        from app.schemas.claim_card import ClaimRawSourceLink

        threshold = max(0, int(settings.SELECTED_SCOPE_DIRECT_CLAIM_LIMIT))
        if threshold <= 0:
            return None
        claim_id_rows = (
            db.query(ClaimRawSourceLink.claim_id)
            .join(ClaimCard, ClaimCard.id == ClaimRawSourceLink.claim_id)
            .filter(
                ClaimCard.status == "active",
                ClaimRawSourceLink.source_id.in_(selected),
            )
            .distinct()
            .limit(threshold + 1)
            .all()
        )
        if len(claim_id_rows) > threshold:
            return None
        claim_ids = [row[0] for row in claim_id_rows]
        if not claim_ids:
            return []
        claims = (
            db.query(ClaimCard)
            .filter(ClaimCard.id.in_(claim_ids), ClaimCard.status == "active")
            .order_by(ClaimCard.confidence.desc(), ClaimCard.created_at.desc())
            .all()
        )
        now = datetime.now(timezone.utc)
        eligible: List[ClaimCard] = []
        for claim in claims:
            if exclude_contradicted and bool(getattr(claim, "is_contradicted", False)):
                continue
            valid_until = getattr(claim, "valid_until", None)
            if valid_until is not None and valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            if exclude_expired and valid_until is not None and valid_until < now:
                continue
            eligible.append(claim)
        return eligible
    except Exception as exc:
        logger.debug(f"[Harness] selected-source local retrieval skipped: {exc}")
        return None


_QUOTE_MAX_CHARS = 240


def _truncate(text: Optional[str], n: int = _QUOTE_MAX_CHARS) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) <= n:
        return t
    return t[: n - 1] + "…"


def _format_horizon(h: Optional[str]) -> str:
    mapping = {
        "immediate": "即时",
        "short": "短期",
        "medium": "中期",
        "long": "长期",
        "unspecified": "",
    }
    return mapping.get((h or "").lower(), h or "")


def _claim_evidence_payload(
    claim: ClaimCard,
    preferred_source_ids: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    selected = set(clean_source_ids(preferred_source_ids))
    try:
        links = list(getattr(claim, "evidence_links", None) or [])
    except Exception:
        links = []
    spans = [
        span
        for link in links
        if (span := getattr(link, "evidence_span", None)) is not None
    ]
    if selected:
        spans = [span for span in spans if getattr(span, "source_id", None) in selected]
    if spans:
        span = spans[0]
        return {
            "evidence_span_id": getattr(span, "id", None),
            "source_id": getattr(span, "source_id", None),
            "span_type": getattr(span, "span_type", None),
            "content": getattr(span, "content", None),
            "char_start": getattr(span, "char_start", None),
            "char_end": getattr(span, "char_end", None),
            "page_or_timestamp": getattr(span, "page_or_timestamp", None),
            "confidence": getattr(span, "confidence", None),
            "extraction_method": getattr(span, "extraction_method", None),
        }

    try:
        raw_links = list(getattr(claim, "raw_source_links", None) or [])
    except Exception:
        raw_links = []
    source_ids = [
        source_id
        for link in raw_links
        if (source_id := getattr(link, "source_id", None))
        and (not selected or source_id in selected)
    ]
    if not source_ids:
        return None
    return {"source_id": source_ids[0]}


def _build_claim_block(
    idx: int,
    claim: ClaimCard,
    preferred_source_ids: Optional[List[str]] = None,
) -> tuple[str, Citation]:
    """把单条 claim 渲染成结构化上下文块，并产出对应 Citation。"""
    stance = _stance_of(claim)
    stance_label = {
        "bullish": "看多",
        "bearish": "看空",
        "neutral": "中性",
        "conditional": "条件性",
    }.get(stance, stance)

    trust = float(getattr(claim, "source_trust_weight", 0.5) or 0.5)
    conf = float(getattr(claim, "confidence", 0.5) or 0.5)
    horizon = _format_horizon(getattr(claim, "time_horizon", None))
    summary = (getattr(claim, "summary", "") or "").strip()
    full_quote = _truncate(getattr(claim, "full_quote", None))
    evidence = _claim_evidence_payload(claim, preferred_source_ids=preferred_source_ids)
    evidence_quote = _truncate(evidence.get("content") if evidence else None)
    conditions = getattr(claim, "conditions", None) or []
    invalidations = getattr(claim, "invalidation_conditions", None) or []

    lines = [f"[{idx}] **{stance_label}** — {summary}"]
    meta_parts = [f"信任 {trust:.2f}", f"置信 {conf:.2f}"]
    if horizon:
        meta_parts.append(f"时域 {horizon}")
    lines.append(f"    · {' · '.join(meta_parts)}")
    if evidence_quote:
        lines.append(f"    - Evidence span: {evidence_quote}")
    elif full_quote:
        lines.append(f"    · 原文引用：「{full_quote}」")
    if conditions:
        cond_txt = "；".join(str(c) for c in conditions[:3])
        lines.append(f"    · 成立条件：{cond_txt}")
    if invalidations:
        inv_txt = "；".join(str(c) for c in invalidations[:3])
        lines.append(f"    · 失效条件：{inv_txt}")

    citation = Citation(
        index=idx,
        claim_id=getattr(claim, "id", ""),
        stance=stance,
        summary=summary,
        short_quote=evidence_quote or full_quote or _truncate(summary, 120),
        trust_weight=trust,
        confidence=float((evidence or {}).get("confidence") or conf),
        time_horizon=getattr(claim, "time_horizon", None),
        speaker_id=getattr(claim, "speaker_id", None),
        geographic_scope=list(getattr(claim, "geographic_scope", None) or []),
        valid_from=getattr(claim, "valid_from", None).isoformat() if getattr(claim, "valid_from", None) else None,
        valid_until=getattr(claim, "valid_until", None).isoformat() if getattr(claim, "valid_until", None) else None,
        source_id=(evidence or {}).get("source_id"),
        evidence_span_id=(evidence or {}).get("evidence_span_id"),
        span_type=(evidence or {}).get("span_type"),
        char_start=(evidence or {}).get("char_start"),
        char_end=(evidence or {}).get("char_end"),
        page_or_timestamp=(evidence or {}).get("page_or_timestamp"),
        extraction_method=(evidence or {}).get("extraction_method"),
    )
    return "\n".join(lines), citation


def _format_market_snapshot(data: Dict[str, Any]) -> str:
    if not data:
        return ""
    asset = data.get("asset", "?")
    price = data.get("price")
    chg1d = data.get("price_change_1d")
    chg7d = data.get("price_change_7d")
    trend = data.get("trend_direction")
    rsi = data.get("rsi_14")
    snap_at = data.get("snapshot_at", "")
    parts = [f"资产 {asset}"]
    if price is not None:
        parts.append(f"价格 {price}")
    if chg1d is not None:
        parts.append(f"1D {chg1d:+.2%}" if abs(float(chg1d)) < 1 else f"1D {chg1d:+.2f}%")
    if chg7d is not None:
        parts.append(f"7D {chg7d:+.2%}" if abs(float(chg7d)) < 1 else f"7D {chg7d:+.2f}%")
    if trend:
        parts.append(f"趋势 {trend}")
    if rsi is not None:
        parts.append(f"RSI {rsi:.1f}")
    line = " | ".join(parts)
    return f"## 实时行情快照\n- {line}\n- 更新时间：{snap_at}"


def _query_terms(query: str) -> list[str]:
    text = (query or "").strip()
    if not text:
        return []
    terms = [part for part in re.split(r"[\s,，。；;：:！!？?\(\)（）\[\]【】]+", text) if len(part) >= 2]
    terms.extend(re.findall(r"[A-Za-z0-9_\-]{3,}", text))
    seen = set()
    clean: list[str] = []
    for term in terms:
        value = term.strip()
        if value and value not in seen:
            clean.append(value)
            seen.add(value)
    return clean[:12]


def _source_evidence_snippet(src: Any, query: str, max_chars: int = 700) -> dict[str, Any]:
    raw_text = (getattr(src, "raw_text", None) or "").strip()
    summary = (getattr(src, "article_summary", None) or "").strip()
    if raw_text:
        start = 0
        lowered = raw_text.lower()
        for term in _query_terms(query):
            pos = lowered.find(term.lower())
            if pos >= 0:
                start = max(0, pos - 160)
                break
        end = min(len(raw_text), start + max_chars)
        content = raw_text[start:end].strip()
        return {
            "span_type": "raw_text",
            "content": content,
            "char_start": start,
            "char_end": start + len(content),
            "page_or_timestamp": None,
            "confidence": 1.0,
            "extraction_method": "source_scope_snippet",
        }
    if summary:
        content = summary[:max_chars].strip()
        return {
            "span_type": "article_summary",
            "content": content,
            "char_start": None,
            "char_end": None,
            "page_or_timestamp": None,
            "confidence": 0.75,
            "extraction_method": "source_scope_summary",
        }
    return {
        "span_type": "missing",
        "content": "",
        "char_start": None,
        "char_end": None,
        "page_or_timestamp": None,
        "confidence": 0.0,
        "extraction_method": "none",
    }


def _datetime_iso(value: Any) -> str | None:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return None
    text = str(value).strip()
    return text or None


def _build_source_scope_block(db: Session, source_ids: Optional[List[str]], query: str = "") -> tuple[str, Dict[str, Any]]:
    selected = clean_source_ids(source_ids)
    if not selected:
        return "", {"mode": "global", "requested_source_ids": [], "matched_source_ids": []}

    try:
        from app.schemas.raw_source import RawSource
        sources = db.query(RawSource).filter(RawSource.id.in_(selected)).all()
    except Exception as exc:
        logger.warning(f"[Harness] source scope lookup failed: {exc}")
        return "", {
            "mode": "selected_sources",
            "requested_source_ids": selected,
            "matched_source_ids": [],
            "error": str(exc)[:200],
        }

    by_id = {s.id: s for s in sources}
    ordered_sources = [by_id[sid] for sid in selected if sid in by_id]
    lines = ["## 当前限定资料范围"]
    lines.append("本次回答优先且仅应基于下列用户选定资料；若资料未形成结构化观点，应说明只能基于原文摘要/片段作保守判断。")
    source_cards = []
    for idx, src in enumerate(ordered_sources[:12], start=1):
        title = (getattr(src, "title", None) or src.id).strip()
        status = getattr(src, "status", "") or "unknown"
        published_at = _datetime_iso(getattr(src, "published_at", None))
        ingested_at = _datetime_iso(getattr(src, "ingested_at", None))
        raw_text = (getattr(src, "raw_text", None) or "").strip()
        summary = (getattr(src, "article_summary", None) or "").strip()
        evidence = _source_evidence_snippet(src, query)
        excerpt = evidence.get("content") or summary or raw_text[:700]
        if len(excerpt) > 900:
            excerpt = excerpt[:900] + "..."
        source_meta = f"[S{idx}] {title} | source_id={src.id} | status={status}"
        if published_at:
            source_meta += f" | published_at={published_at}"
        lines.append(source_meta)
        if excerpt:
            lines.append(f"    摘要/片段：{excerpt}")
        else:
            lines.append("    摘要/片段：暂无可用文本，可能仍在解析或处理失败。")
        source_cards.append({
            "source_id": src.id,
            "title": title,
            "status": status,
            "published_at": published_at,
            "ingested_at": ingested_at,
            "has_summary": bool(summary),
            "has_raw_text": bool(raw_text),
            "text_chars": len(raw_text),
            "evidence": evidence,
        })

    missing = [sid for sid in selected if sid not in by_id]
    if missing:
        lines.append(f"未找到的 source_id：{', '.join(missing[:8])}")

    return "\n".join(lines), {
        "mode": "selected_sources",
        "requested_source_ids": selected,
        "matched_source_ids": [s.id for s in ordered_sources],
        "missing_source_ids": missing,
        "sources": source_cards,
    }


# ══════════════════════════════════════════════════════════════════════════
# 3. 主入口
# ══════════════════════════════════════════════════════════════════════════

def build_chat_context(
    db: Session,
    query: str,
    asset_filter: Optional[Union[str, List[str]]] = None,
    limit: int = 8,
    include_market: bool = True,
    source_ids: Optional[List[str]] = None,
    exclude_expired: bool = True,
    exclude_contradicted: bool = True,
) -> ChatContext:
    """Chat 路径的完整 Harness 入口。

    输出已拼好的 context 文本 + citations + 行情快照 + 元数据。
    """
    local_scope_claims = _small_selected_source_claims(
        db,
        source_ids,
        exclude_expired=exclude_expired,
        exclude_contradicted=exclude_contradicted,
    )
    if local_scope_claims is None:
        claims, dist, balanced, scanned = balanced_semantic_search(
            db=db,
            query=query,
            limit=limit,
            asset_filter=asset_filter,
            source_ids=source_ids,
            exclude_expired=exclude_expired,
            exclude_contradicted=exclude_contradicted,
        )
        search_tier = sem_search.get_last_search_tier()
    else:
        from app.utils.claim_post_processor import post_process_candidates

        local_result = post_process_candidates(
            candidates=local_scope_claims,
            query=query,
            top_k=limit,
            use_reranker=None,
            use_stance_balance=True,
            context="chat_selected_scope_local",
        )
        claims = local_result.claims
        dist = local_result.stance_distribution
        balanced = local_result.balanced_applied
        scanned = local_result.total_scanned
        search_tier = "selected-source-local"

    # G15: Parent-Child Retrieval — 命中后附同源邻居 claim（默认关闭）
    if not claims and source_ids and local_scope_claims is None:
        claims = _claims_from_selected_sources(
            db,
            source_ids,
            limit,
            exclude_expired=exclude_expired,
            exclude_contradicted=exclude_contradicted,
        )
        if claims:
            dist = {}
            for claim in claims:
                stance = _stance_of(claim)
                dist[stance] = dist.get(stance, 0) + 1
            scanned = len(claims)
            balanced = False

    parent_neighbors_added = 0
    if claims:
        try:
            from app.config import settings as _settings
            if getattr(_settings, "ENABLE_PARENT_CONTEXT", False):
                from app.services.parent_context import attach_parent_neighbors
                _before = len(claims)
                claims = attach_parent_neighbors(
                    db,
                    claims,
                    neighbors_per_source=int(getattr(_settings, "PARENT_CONTEXT_NEIGHBORS", 2)),
                    max_sources=int(getattr(_settings, "PARENT_CONTEXT_MAX_SOURCES", 3)),
                )
                parent_neighbors_added = len(claims) - _before
        except Exception as _e:
            logger.debug(f"[Harness] parent-context skipped: {_e}")

    blocks: List[str] = []
    citations: List[Citation] = []
    source_scope_block, source_scope_meta = _build_source_scope_block(db, source_ids, query=query)
    if source_scope_block:
        blocks.append(source_scope_block)

    # 市场快照（若识别到资产）
    # P2-2: asset_filter 可能是单个 str 或 List[str]（多资产问题）
    market_data: Optional[Dict[str, Any]] = None
    if include_market and asset_filter and not clean_source_ids(source_ids):
        # 规范化为列表：单资产时只包 1 个元素；多资产时逐个抓快照
        if isinstance(asset_filter, str):
            _assets_for_snapshot = [asset_filter]
        else:
            try:
                _assets_for_snapshot = [a for a in asset_filter if a]
            except Exception:
                _assets_for_snapshot = [str(asset_filter)]
        try:
            from app.mcps.market_data_mcp import MarketDataMCP
            _mcp = MarketDataMCP(db)
            _snapshots: list = []
            for _aname in _assets_for_snapshot[:3]:  # 最多拉 3 个快照，避免超长 context
                _resp = _mcp.get_market_state(asset=str(_aname).upper())
                if _resp and _resp.data:
                    _snapshots.append(_resp.data)
                    _market_txt = _format_market_snapshot(_resp.data)
                    if _market_txt:
                        blocks.append(_market_txt)
            if _snapshots:
                # 为兼容前端：market_snapshot 保持单 dict（第一个）
                # 额外挂 market_snapshots_all 便于未来展示多资产
                market_data = _snapshots[0]
                if len(_snapshots) > 1:
                    market_data = dict(market_data)  # 拷贝后更新
                    market_data["_all"] = _snapshots
        except Exception as e:
            logger.debug(f"[Harness] market snapshot skipped: {e}")

    # 私域观点块 —— B1: token 预算截断，避免撑爆 LLM context window
    dropped_claims = 0
    if claims:
        from app.utils.token_budget import (
            estimate_tokens,
            fit_blocks_to_budget,
            DEFAULT_CONTEXT_BUDGET,
        )
        # 已占用（market snapshot）+ 预算保留
        prefix_tokens = sum(estimate_tokens(b) for b in blocks)
        claim_budget = max(1500, DEFAULT_CONTEXT_BUDGET - prefix_tokens)

        # 预渲染每条 claim 并按序提交预算器（claims 已按综合评分降序）
        per_claim_blocks: List[str] = []
        per_claim_citations: List[Citation] = []
        for i, c in enumerate(claims, start=1):
            block, cit = _build_claim_block(i, c, preferred_source_ids=source_ids)
            per_claim_blocks.append(block)
            per_claim_citations.append(cit)

        kept_blocks, used_tokens, dropped = fit_blocks_to_budget(
            per_claim_blocks,
            budget_tokens=claim_budget,
            keep_first=1,  # 最高分 claim 无条件保留
        )
        kept_count = len(kept_blocks)
        dropped_claims = dropped

        # 组合实际入选的 claim + citation（编号仍按原始顺序 1..kept_count）
        claim_lines: List[str] = []
        header_bits = [
            f"## 私域知识库参考（{kept_count}/{len(claims)} 条 · "
            f"看多 {dist.get('bullish', 0)} / 看空 {dist.get('bearish', 0)} / "
            f"中性 {dist.get('neutral', 0) + dist.get('conditional', 0)}）"
        ]
        if dropped_claims > 0:
            header_bits.append(
                f"> 注：受上下文预算限制，低分的 {dropped_claims} 条已省略"
            )
        claim_lines.append("\n".join(header_bits))
        claim_lines.append(
            "每条观点以 [N] 编号。回答时请在引用对应内容处标注 [N]；若多条支撑同一结论，可用 [1][3]。"
        )
        claim_lines.extend(kept_blocks)
        citations.extend(per_claim_citations[:kept_count])
        blocks.append("\n".join(claim_lines))

        if dropped_claims:
            logger.info(
                f"[Harness] context budget trimmed {dropped_claims}/{len(claims)} "
                f"claims (used {used_tokens}/{claim_budget} tokens)"
            )

    context_block = "\n\n".join(blocks)

    # B1: 计算最终 token 总量用于诊断
    from app.utils.token_budget import estimate_tokens as _et
    total_tokens = _et(context_block)

    chat_context = ChatContext(
        context_block=context_block,
        citations=citations,
        market_snapshot=market_data,
        stance_distribution=dist,
        total_candidates_scanned=scanned,
        balanced_applied=balanced,
        search_tier=search_tier,
        context_tokens=total_tokens,
        context_dropped=dropped_claims,
        parent_neighbors_added=parent_neighbors_added,
        quality_filters={
            "exclude_expired": exclude_expired,
            "exclude_contradicted": exclude_contradicted,
            "retrieval_mode": search_tier,
            "support_evidence_policy": (
                "Default chat context excludes expired or contradicted active claims; "
                "maintenance pages can still inspect archived/contradicted records."
            ),
        },
    )
    chat_context.source_scope = source_scope_meta
    return chat_context


# ══════════════════════════════════════════════════════════════════════════
# 4. System Prompt（带归因 + refusal 规则）
# ══════════════════════════════════════════════════════════════════════════

CHAT_SYSTEM_PROMPT_V2 = """你是一位专业的金融研究助理，服务于一位个人投资研究者。
你的核心价值不是"无所不知的模型"，而是"能忠实利用用户私域知识库的助手"。

## 行为准则（严格遵守）

1. **来源归因**：凡是源自"私域知识库参考"的事实或观点，在文中对应位置用 `[N]` 标注引用编号，
   例如："短期看多的核心逻辑是 ETF 资金持续流入 [1][3]"。多条支撑可用 `[1][2]`。
2. **区分来源**：若某段内容是你自己的通用知识而非来自知识库，用"（一般而言 / 背景补充）"等措辞显式声明。
3. **refusal 机制**：若知识库和行情快照中都没有相关证据，不要凭空编造；应直接说明：
   "私域库当前没有覆盖该问题，以下是通用背景，仅供参考"。
4. **对立观点并存**：若上下文同时出现看多与看空观点，请双方都复述并说明条件差异，不要单边倒。
5. **使用原文**：参考"原文引用"字段时可以直接短句引用，让用户感到可追溯。
6. **可信度校准**：信任 < 0.4 或 置信 < 0.5 的观点在叙述时应降权措辞（如"亦有声音认为"），
   不要与高信任观点等价呈现。
7. **失效条件**：若某观点附有"失效条件"，判断当前市场是否已触发 → 若已触发，在回答中明示该观点可能已失效。
8. **时间基准**：回答正文第一段必须明确写出本次判断的参考时间，例如"截至 2026-05-26 14:30 CST"。
   不要只说"当前、近期、最近"，除非同一句里已经限定了具体日期、时间窗口或数据更新时间。
   若用户问题指定了另一个"截至/基于"日期，或证据有独立发布日期，必须把系统参考时间、问题的 as-of 日期和证据日期区分清楚，不能用当前时间替换用户指定的时间边界。
9. **结论先行**：先直接回答用户问题，再解释依据。不要把"理由/检索过程/证据列表"放在正文之前。
10. **引用方式**：引用不是独立罗列的装饰，而是把 `[N]` 放在被它支撑的分析句旁边，像论文交叉引用一样自然嵌入。
11. **简洁**：日常问答建议使用以下顺序：
   - 首段：截至某个具体时间的直接判断或回答。
   - 正文：2-5 个自然段说明关键逻辑，相关句子旁标注 `[N]`。
   - 末段：必要的不确定性、缺口或下一步确认点。
   避免先堆一组"理由："再给正文。

## 不要做
- 不要主动生成结构化简报（除非用户明确要求深度研究）。
- 不要套用"一、二、三"的报告体。
- 不要忽略用户母语（中文问题用中文回答）。
"""


__all__ = [
    "build_chat_context",
    "balanced_semantic_search",
    "ChatContext",
    "Citation",
    "CHAT_SYSTEM_PROMPT_V2",
]
