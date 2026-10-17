from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, List
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from ulid import ULID

from app.dependencies import get_db
from app.services.speaker_repository import SpeakerRepository
from app.schemas.speaker_profile import SpeakerProfile

router = APIRouter()


@router.get("", response_model=dict)
def list_speakers(
    name: Optional[str] = Query(None, description="姓名模糊搜索"),
    domain: Optional[str] = Query(None, description="领域标签过滤"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """搜索来源画像列表"""
    repo = SpeakerRepository(db)
    items, total = repo.search(name=name, domain=domain, skip=offset, limit=limit)
    return {
        "speakers": [_to_dict(s) for s in items],
        "total": total,
        "has_more": (offset + limit) < total,
    }


@router.get("/{speaker_id}", response_model=dict)
async def get_speaker(speaker_id: str, db: Session = Depends(get_db)):
    """获取单个来源画像"""
    repo = SpeakerRepository(db)
    s = repo.get(speaker_id)
    if not s:
        raise HTTPException(status_code=404, detail="Speaker not found")
    return _to_dict(s)


@router.post("", response_model=dict, status_code=201)
async def create_speaker(
    body: dict,
    db: Session = Depends(get_db),
):
    """手工创建来源画像"""
    now = datetime.now(timezone.utc)
    speaker = SpeakerProfile(
        id=str(ULID()),
        name=body.get("name", "Unknown"),
        aliases=body.get("aliases", []),
        affiliation=body.get("affiliation"),
        role=body.get("role"),
        domain_tags=body.get("domain_tags", []),
        trust_weight=float(body.get("trust_weight", 0.5)),
        track_record_summary=body.get("track_record_summary"),
        style_notes=body.get("style_notes"),
        status="active",
        total_claims=0,
        verified_claims=0,
        created_at=now,
        updated_at=now,
    )
    repo = SpeakerRepository(db)
    saved = repo.create(speaker)
    return _to_dict(saved)


@router.patch("/{speaker_id}", response_model=dict)
async def update_speaker(
    speaker_id: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """更新来源画像"""
    repo = SpeakerRepository(db)
    s = repo.get(speaker_id)
    if not s:
        raise HTTPException(status_code=404, detail="Speaker not found")
    allowed = {"name", "aliases", "affiliation", "role", "domain_tags",
               "trust_weight", "track_record_summary", "style_notes", "status"}
    update_data = {k: v for k, v in body.items() if k in allowed}
    update_data["updated_at"] = datetime.now(timezone.utc)
    updated = repo.update(s, update_data)
    return _to_dict(updated)


@router.delete("/{speaker_id}", status_code=204)
async def delete_speaker(speaker_id: str, db: Session = Depends(get_db)):
    """软删除来源画像"""
    repo = SpeakerRepository(db)
    s = repo.get(speaker_id)
    if not s:
        raise HTTPException(status_code=404, detail="Speaker not found")
    repo.update(s, {"status": "archived", "updated_at": datetime.now(timezone.utc)})


@router.post("/{speaker_id}/merge", response_model=dict)
async def merge_speakers(
    speaker_id: str,
    body: dict,
    db: Session = Depends(get_db),
):
    """合并两个发言人：将 source_id 的别名和 claims 合并到 speaker_id。

    body: {"source_speaker_id": "xxx"}
    source speaker 会被 archived，其所有 claims 的 speaker_id 会指向 target。
    """
    from app.schemas.claim_card import ClaimCard
    from app.database import safe_commit

    target_id = speaker_id
    source_id = body.get("source_speaker_id")
    if not source_id:
        raise HTTPException(400, "source_speaker_id required")

    repo = SpeakerRepository(db)
    target = repo.get(target_id)
    source = repo.get(source_id)
    if not target:
        raise HTTPException(404, f"Target speaker {target_id} not found")
    if not source:
        raise HTTPException(404, f"Source speaker {source_id} not found")

    # Merge aliases
    merged_aliases = list(set((target.aliases or []) + (source.aliases or []) + [source.name]))
    if target.name in merged_aliases:
        merged_aliases.remove(target.name)
    target.aliases = merged_aliases

    # Merge domain tags
    target.domain_tags = list(set((target.domain_tags or []) + (source.domain_tags or [])))

    # Merge claim counts
    target.total_claims = (target.total_claims or 0) + (source.total_claims or 0)
    target.verified_claims = (target.verified_claims or 0) + (source.verified_claims or 0)

    # Reassign all claims from source to target
    reassigned = db.query(ClaimCard).filter(ClaimCard.speaker_id == source_id).update(
        {ClaimCard.speaker_id: target_id}
    )

    # Archive source
    source.status = "archived"
    source.updated_at = datetime.now(timezone.utc)
    target.updated_at = datetime.now(timezone.utc)

    safe_commit(db)

    return {
        "merged": True,
        "target": _to_dict(target),
        "source_archived": source_id,
        "claims_reassigned": reassigned,
    }


@router.get("/{speaker_id}/claims", response_model=dict)
async def get_speaker_claims(
    speaker_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """获取发言人的所有观点"""
    from app.schemas.claim_card import ClaimCard

    repo = SpeakerRepository(db)
    s = repo.get(speaker_id)
    if not s:
        raise HTTPException(404, "Speaker not found")

    claims_q = db.query(ClaimCard).filter(ClaimCard.speaker_id == speaker_id)
    total = claims_q.count()
    claims = claims_q.order_by(ClaimCard.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "speaker": _to_dict(s),
        "total_claims": total,
        "claims": [{
            "id": c.id,
            "summary": c.summary,
            "full_quote": c.full_quote,
            "claim_type": c.claim_type,
            "stance": c.stance,
            "confidence": c.confidence,
            "asset_scope": c.asset_scope or [],
            "time_horizon": c.time_horizon,
            "valid_until": c.valid_until.isoformat() if c.valid_until else None,
            "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in claims],
    }


@router.post("/{speaker_id}/review", response_model=dict)
async def review_speaker(
    speaker_id: str,
    db: Session = Depends(get_db),
):
    """触发 LLM 对发言人进行画像点评，更新 speaker profile 的多维字段。"""
    from app.schemas.claim_card import ClaimCard
    from app.skills.speaker_review_skill import (
        generate_speaker_review, format_claims_for_review,
    )
    from app.database import safe_commit

    repo = SpeakerRepository(db)
    s = repo.get(speaker_id)
    if not s:
        raise HTTPException(404, "Speaker not found")

    # Fetch all claims for this speaker
    claims = (
        db.query(ClaimCard)
        .filter(ClaimCard.speaker_id == speaker_id)
        .order_by(ClaimCard.created_at.desc())
        .limit(200)
        .all()
    )
    if not claims:
        raise HTTPException(400, "No claims to review")

    claims_dicts = [{
        "summary": c.summary, "full_quote": c.full_quote,
        "claim_type": c.claim_type, "stance": c.stance,
        "confidence": c.confidence, "asset_scope": c.asset_scope or [],
    } for c in claims]

    claims_text = format_claims_for_review(claims_dicts)
    result = await generate_speaker_review(
        speaker_name=s.name,
        speaker_aliases=s.aliases or [],
        claims_text=claims_text,
        total_claims=len(claims),
        existing_notes=s.style_notes,
    )

    # Persist review results into speaker profile
    now = datetime.now(timezone.utc)
    updates: dict = {"updated_at": now, "last_calibrated_at": now}

    if result.get("overall_assessment"):
        updates["track_record_summary"] = result["overall_assessment"]
    if result.get("usage_recommendation"):
        updates["usage_recommendation"] = result["usage_recommendation"]

    style_parts = []
    for key in ("speaking_style", "analysis_approach", "investment_style", "logic_assessment", "perspective_uniqueness"):
        if result.get(key):
            style_parts.append(f"【{key}】{result[key]}")
    if style_parts:
        updates["style_notes"] = "\n".join(style_parts)

    if result.get("expertise_domains"):
        updates["applicable_domains"] = result["expertise_domains"]
        updates["domain_tags"] = list(set((s.domain_tags or []) + result["expertise_domains"]))

    if result.get("suggested_trust_weight") is not None:
        try:
            updates["trust_weight"] = max(0.0, min(1.0, float(result["suggested_trust_weight"])))
        except (ValueError, TypeError):
            pass
    if result.get("suggested_perspective_value") is not None:
        try:
            updates["perspective_value"] = max(0.0, min(1.0, float(result["suggested_perspective_value"])))
        except (ValueError, TypeError):
            pass
    if result.get("suggested_logic_robustness") is not None:
        try:
            updates["logic_robustness"] = max(0.0, min(1.0, float(result["suggested_logic_robustness"])))
        except (ValueError, TypeError):
            pass

    repo.update(s, updates)
    safe_commit(db)

    return {
        "speaker": _to_dict(s),
        "review": result,
        "claims_reviewed": len(claims),
    }


def _to_dict(s: SpeakerProfile) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "aliases": s.aliases or [],
        "affiliation": s.affiliation,
        "role": s.role,
        "domain_tags": s.domain_tags or [],
        "trust_weight": s.trust_weight,
        "track_record_summary": s.track_record_summary,
        "style_notes": s.style_notes,
        "hit_rate": s.hit_rate,
        "total_claims": s.total_claims or 0,
        "verified_claims": s.verified_claims or 0,
        "status": s.status,
        "perspective_value": getattr(s, 'perspective_value', 0.5),
        "logic_robustness": getattr(s, 'logic_robustness', 0.5),
        "applicable_domains": getattr(s, 'applicable_domains', []) or [],
        "usage_recommendation": getattr(s, 'usage_recommendation', None),
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
