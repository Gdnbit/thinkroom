from fastapi import APIRouter, Depends, Query, HTTPException, Body
from typing import Optional
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.claim import ClaimCardResponse, ClaimSearchResponse
from app.services.claim_repository import ClaimRepository
from app.services import semantic_search as sem_search

router = APIRouter()


@router.get("/semantic-search", response_model=dict)
async def semantic_search_claims(
    q: str = Query(..., description="语义搜索查询"),
    asset: Optional[str] = Query(None, description="资产范围过滤"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """语义搜索观点卡片（有 embedding 时用向量搜索，否则退化为关键词搜索）"""
    results = sem_search.semantic_search(
        db=db,
        query=q,
        limit=limit,
        asset_filter=asset,
        status_filter="active",
    )
    return {
        "query": q,
        "results": [ClaimCardResponse.model_validate(c).model_dump() for c in results],
        "count": len(results),
        "search_type": sem_search.get_last_search_tier(),
    }



@router.get("", response_model=ClaimSearchResponse)
async def search_claims(
    q: Optional[str] = Query(None, description="搜索关键词"),
    asset: Optional[str] = Query(None, description="资产范围"),
    theme: Optional[str] = Query(None, description="主题范围"),
    status: Optional[str] = Query("active", description="状态过滤"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """搜索观点卡片"""
    repo = ClaimRepository(db)
    items, total = repo.search(
        q=q, asset=asset, theme=theme, status=status,
        skip=offset, limit=limit,
    )
    return ClaimSearchResponse(
        claims=[ClaimCardResponse.model_validate(c) for c in items],
        total=total,
        has_more=(offset + limit) < total,
    )


@router.get("/{claim_id}", response_model=ClaimCardResponse)
async def get_claim(
    claim_id: str,
    db: Session = Depends(get_db),
):
    """获取单个观点卡片"""
    repo = ClaimRepository(db)
    claim = repo.get(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return ClaimCardResponse.model_validate(claim)


# ── G8 · 引用反查 ─────────────────────────────────────────


@router.get("/{claim_id}/citation")
async def get_claim_citation(
    claim_id: str,
    excerpt_chars: int = Query(800, ge=100, le=5000, description="原文摘要字符数上限"),
    db: Session = Depends(get_db),
):
    """获取 claim 的"引用反查包"——claim 详情 + 关联 raw_source + evidence_span 片段.

    用户在 chat 里看到 [N] 引用标记，点击 → 调用此端点 → 弹窗展示：
    - claim 摘要 / 立场 / 置信度 / 时间敏感度 / asset_scope
    - 来源文档列表（标题 / 作者 / 发布时间 / URL / 摘要片段）
    - 若有 evidence_span：高亮原文具体定位（char_start/char_end / page_or_timestamp）
    """
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink, ClaimEvidenceLink
    from app.schemas.raw_source import RawSource
    from app.schemas.evidence_span import EvidenceSpan
    from app.schemas.speaker_profile import SpeakerProfile

    claim = db.query(ClaimCard).filter(ClaimCard.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")

    # ── 1. 关联 raw_sources ───
    source_links = (
        db.query(ClaimRawSourceLink)
        .filter(ClaimRawSourceLink.claim_id == claim_id)
        .all()
    )
    source_ids = [l.source_id for l in source_links]
    sources_data = []
    if source_ids:
        sources = (
            db.query(RawSource).filter(RawSource.id.in_(source_ids)).all()
        )
        for src in sources:
            excerpt = (src.raw_text or "")[:excerpt_chars]
            truncated = bool(src.raw_text and len(src.raw_text) > excerpt_chars)
            sources_data.append({
                "id": src.id,
                "source_type": src.source_type,
                "title": src.title,
                "url": src.original_url,
                "publisher": src.publisher,
                "speaker_hint": src.speaker_hint,
                "published_at": src.published_at.isoformat() if src.published_at else None,
                "language": src.language,
                "status": src.status,
                "excerpt": excerpt,
                "excerpt_truncated": truncated,
                "raw_text_length": len(src.raw_text) if src.raw_text else 0,
                "link_type": next(
                    (l.link_type for l in source_links if l.source_id == src.id), None
                ),
            })

    # ── 2. 关联 evidence_spans（精确定位）────
    evidence_links = (
        db.query(ClaimEvidenceLink)
        .filter(ClaimEvidenceLink.claim_id == claim_id)
        .all()
    )
    evidence_data = []
    if evidence_links:
        span_ids = [el.evidence_span_id for el in evidence_links]
        spans = db.query(EvidenceSpan).filter(EvidenceSpan.id.in_(span_ids)).all()
        rel_map = {el.evidence_span_id: el.relevance_score for el in evidence_links}
        for sp in spans:
            evidence_data.append({
                "id": sp.id,
                "source_id": sp.source_id,
                "span_type": sp.span_type,
                "content": sp.content,
                "page_or_timestamp": sp.page_or_timestamp,
                "char_start": sp.char_start,
                "char_end": sp.char_end,
                "confidence": sp.confidence,
                "extraction_method": sp.extraction_method,
                "relevance_score": rel_map.get(sp.id, 1.0),
            })

    # ── 3. 发言人信息（如有） ────
    speaker_data = None
    if claim.speaker_id:
        sp_profile = db.query(SpeakerProfile).filter(
            SpeakerProfile.id == claim.speaker_id
        ).first()
        if sp_profile:
            speaker_data = {
                "id": sp_profile.id,
                "name": sp_profile.name,
                "trust_weight": sp_profile.trust_weight,
                "domain_tags": sp_profile.domain_tags,
                "affiliation": sp_profile.affiliation,
            }

    return {
        "claim": {
            "id": claim.id,
            "summary": claim.summary,
            "full_quote": claim.full_quote,
            "claim_type": claim.claim_type,
            "stance": claim.stance,
            "time_horizon": claim.time_horizon,
            "asset_scope": claim.asset_scope,
            "theme_scope": claim.theme_scope,
            "confidence": claim.confidence,
            "source_trust_weight": claim.source_trust_weight,
            "is_contradicted": claim.is_contradicted,
            "contradiction_notes": claim.contradiction_notes,
            "status": claim.status,
            "valid_from": claim.valid_from.isoformat() if claim.valid_from else None,
            "valid_until": claim.valid_until.isoformat() if claim.valid_until else None,
            "time_sensitive": claim.time_sensitive,
            "expiry_hint": claim.expiry_hint,
            "version": claim.version,
            "created_at": claim.created_at.isoformat() if claim.created_at else None,
        },
        "speaker": speaker_data,
        "sources": sources_data,
        "evidence_spans": evidence_data,
    }
