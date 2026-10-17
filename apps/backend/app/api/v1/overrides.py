"""User Override API — 人工修正与审计留痕

支持对 speaker / event / claim / adjudication / briefing / decision_snapshot 进行修正，
所有修正自动留痕到 user_overrides 表，用于后续校准系统。

同时对 EventCard 的 truth_status / outcome_status 修正会同步更新 EventCard 自身字段。
"""
import json
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ulid import ULID
from loguru import logger

from app.database import safe_commit
from app.dependencies import get_db
from app.schemas.user_override import UserOverride
from app.schemas.event_card import EventCard
from app.schemas.speaker_profile import SpeakerProfile

router = APIRouter()


# ── 请求模型 ────────────────────────────────────────────────────

class OverrideRequest(BaseModel):
    target_type: str       # speaker | event | claim | adjudication | briefing | decision_snapshot
    target_id: str         # 被修正对象的 ID
    field_name: str        # 被修正的字段名
    new_value: str         # 修正后的值
    reason: Optional[str] = None
    override_type: str = "correction"  # correction | annotation | adoption_feedback | learning_mark


class AdoptionFeedbackRequest(BaseModel):
    target_type: str       # briefing | adjudication
    target_id: str
    adopted: bool
    user_action: Optional[str] = None    # 用户实际采取的行动
    reason: Optional[str] = None


class OverrideResponse(BaseModel):
    id: str
    target_type: str
    target_id: str
    field_name: str
    old_value: Optional[str]
    new_value: str
    override_type: str
    created_at: str


# ── 创建修正 ────────────────────────────────────────────────────

@router.post("", response_model=OverrideResponse)
def create_override(req: OverrideRequest, db: Session = Depends(get_db)):
    """创建一条修正记录，同时同步更新目标对象的字段"""
    now = datetime.now(timezone.utc)
    old_value = None

    # 如果修正事件的 truth_status / outcome_status，同步更新 EventCard
    if req.target_type == "event" and req.field_name in ("truth_status", "outcome_status"):
        event = db.query(EventCard).filter(EventCard.id == req.target_id).first()
        if not event:
            raise HTTPException(status_code=404, detail=f"EventCard {req.target_id} 不存在")

        old_value = getattr(event, req.field_name, None)

        if req.field_name == "truth_status":
            event.user_override_truth = req.new_value
            event.truth_status = req.new_value
            event.truth_updated_at = now
            event.truth_source = f"user_override: {req.reason or '手动修正'}"
        elif req.field_name == "outcome_status":
            event.user_override_outcome = req.new_value
            event.outcome_status = req.new_value
            event.outcome_observed_at = now
            event.outcome_notes = req.reason

        event.user_override_notes = req.reason
        event.updated_at = now

    # 如果修正来源可信度
    elif req.target_type == "speaker" and req.field_name == "trust_weight":
        speaker = db.query(SpeakerProfile).filter(SpeakerProfile.id == req.target_id).first()
        if not speaker:
            raise HTTPException(status_code=404, detail=f"SpeakerProfile {req.target_id} 不存在")
        old_value = str(speaker.trust_weight)
        try:
            speaker.trust_weight = float(req.new_value)
        except ValueError:
            raise HTTPException(status_code=400, detail="trust_weight 必须是数字")
        speaker.updated_at = now

    else:
        # 通用修正：只记录，不自动同步（避免对未预期字段造成影响）
        old_value = None

    # 创建修正记录
    override = UserOverride(
        id=str(ULID()),
        target_type=req.target_type,
        target_id=req.target_id,
        field_name=req.field_name,
        old_value=json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
        new_value=req.new_value,
        reason=req.reason,
        override_type=req.override_type,
        extra_data={},
        created_at=now,
        updated_at=now,
    )
    db.add(override)
    safe_commit(db)

    logger.info(
        f"[Override] {req.override_type}: {req.target_type}/{req.target_id}.{req.field_name} "
        f"= {req.new_value} (was {old_value})"
    )

    return OverrideResponse(
        id=override.id,
        target_type=override.target_type,
        target_id=override.target_id,
        field_name=override.field_name,
        old_value=override.old_value,
        new_value=override.new_value,
        override_type=override.override_type,
        created_at=override.created_at.isoformat(),
    )


# ── 采纳反馈（快捷入口）───────────────────────────────────────

@router.post("/adoption-feedback", response_model=OverrideResponse)
def adoption_feedback(req: AdoptionFeedbackRequest, db: Session = Depends(get_db)):
    """用户对系统建议的采纳/不采纳反馈"""
    now = datetime.now(timezone.utc)
    override = UserOverride(
        id=str(ULID()),
        target_type=req.target_type,
        target_id=req.target_id,
        field_name="adoption_feedback",
        old_value=None,
        new_value=json.dumps({"adopted": req.adopted}, ensure_ascii=False),
        reason=req.reason,
        override_type="adoption_feedback",
        extra_data={
            "adopted": req.adopted,
            "user_action": req.user_action,
        },
        created_at=now,
        updated_at=now,
    )
    db.add(override)
    safe_commit(db)

    logger.info(
        f"[Override] adoption_feedback: {req.target_type}/{req.target_id} "
        f"adopted={req.adopted} action={req.user_action}"
    )

    return OverrideResponse(
        id=override.id,
        target_type=override.target_type,
        target_id=override.target_id,
        field_name=override.field_name,
        old_value=override.old_value,
        new_value=override.new_value,
        override_type=override.override_type,
        created_at=override.created_at.isoformat(),
    )


# ── 查询修正历史 ────────────────────────────────────────────────

@router.get("")
def list_overrides(
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    override_type: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """查询修正历史"""
    q = db.query(UserOverride).order_by(UserOverride.created_at.desc())
    if target_type:
        q = q.filter(UserOverride.target_type == target_type)
    if target_id:
        q = q.filter(UserOverride.target_id == target_id)
    if override_type:
        q = q.filter(UserOverride.override_type == override_type)
    overrides = q.limit(limit).all()
    return {
        "total": len(overrides),
        "overrides": [
            {
                "id": o.id,
                "target_type": o.target_type,
                "target_id": o.target_id,
                "field_name": o.field_name,
                "old_value": o.old_value,
                "new_value": o.new_value,
                "reason": o.reason,
                "override_type": o.override_type,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in overrides
        ],
    }


# ── 按目标对象查询修正历史 ──────────────────────────────────────

@router.get("/{target_type}/{target_id}")
def get_overrides_for_target(
    target_type: str,
    target_id: str,
    db: Session = Depends(get_db),
):
    """查询某个对象的所有修正历史"""
    overrides = (
        db.query(UserOverride)
        .filter(UserOverride.target_type == target_type, UserOverride.target_id == target_id)
        .order_by(UserOverride.created_at.desc())
        .all()
    )
    return {
        "target_type": target_type,
        "target_id": target_id,
        "total": len(overrides),
        "overrides": [
            {
                "id": o.id,
                "field_name": o.field_name,
                "old_value": o.old_value,
                "new_value": o.new_value,
                "reason": o.reason,
                "override_type": o.override_type,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in overrides
        ],
    }
