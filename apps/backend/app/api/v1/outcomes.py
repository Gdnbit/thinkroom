from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ulid import ULID

from app.dependencies import get_db
from app.models.outcome import (
    OutcomeCreateRequest, OutcomeUpdateRequest,
    OutcomeResponse, OutcomeListResponse, OutcomeStatsResponse,
)
from app.services.outcome_repository import OutcomeRepository
from app.schemas.outcome_record import OutcomeRecord

router = APIRouter()


@router.get("/stats", response_model=OutcomeStatsResponse)
def get_outcome_stats(db: Session = Depends(get_db)):
    """获取 Outcome 统计（命中率、正确率、平均盈亏）"""
    repo = OutcomeRepository(db)
    return repo.get_stats()


@router.get("", response_model=OutcomeListResponse)
def list_outcomes(
    briefing_id: Optional[str] = Query(None),
    claim_id: Optional[str] = Query(None),
    outcome_type: Optional[str] = Query(None),
    is_correct: Optional[bool] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """列出 Outcome 记录"""
    repo = OutcomeRepository(db)
    items, total = repo.list(
        briefing_id=briefing_id,
        claim_id=claim_id,
        outcome_type=outcome_type,
        is_correct=is_correct,
        skip=offset,
        limit=limit,
    )
    return OutcomeListResponse(
        outcomes=[OutcomeResponse.model_validate(i) for i in items],
        total=total,
        has_more=(offset + limit) < total,
    )


@router.post("", response_model=OutcomeResponse, status_code=201)
async def create_outcome(
    body: OutcomeCreateRequest,
    db: Session = Depends(get_db),
):
    """手工记录一条 Outcome"""
    now = datetime.now(timezone.utc)
    record = OutcomeRecord(
        id=str(ULID()),
        briefing_id=body.briefing_id,
        claim_id=body.claim_id,
        setup_id=body.setup_id,
        outcome_type=body.outcome_type,
        is_correct=body.is_correct,
        actual_direction=body.actual_direction,
        predicted_direction=body.predicted_direction,
        entry_price=body.entry_price,
        exit_price=body.exit_price,
        pnl_pct=body.pnl_pct,
        max_drawdown_pct=body.max_drawdown_pct,
        prediction_made_at=body.prediction_made_at,
        outcome_observed_at=body.outcome_observed_at or now,
        horizon_days=body.horizon_days,
        review_notes=body.review_notes,
        what_went_right=body.what_went_right,
        what_went_wrong=body.what_went_wrong,
        lesson_learned=body.lesson_learned,
        logged_by=body.logged_by,
        created_at=now,
        updated_at=now,
    )
    repo = OutcomeRepository(db)
    saved = repo.create(record)
    # 自动更新关联来源的胜率
    repo.score_and_update_speaker(saved.id)
    return OutcomeResponse.model_validate(saved)


@router.get("/{outcome_id}", response_model=OutcomeResponse)
async def get_outcome(outcome_id: str, db: Session = Depends(get_db)):
    """获取单条 Outcome"""
    repo = OutcomeRepository(db)
    record = repo.get(outcome_id)
    if not record:
        raise HTTPException(status_code=404, detail="Outcome not found")
    return OutcomeResponse.model_validate(record)


@router.patch("/{outcome_id}", response_model=OutcomeResponse)
async def update_outcome(
    outcome_id: str,
    body: OutcomeUpdateRequest,
    db: Session = Depends(get_db),
):
    """更新 Outcome 记录"""
    repo = OutcomeRepository(db)
    record = repo.get(outcome_id)
    if not record:
        raise HTTPException(status_code=404, detail="Outcome not found")
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc)
    updated = repo.update(record, update_data)
    return OutcomeResponse.model_validate(updated)


@router.delete("/{outcome_id}", status_code=204)
async def delete_outcome(outcome_id: str, db: Session = Depends(get_db)):
    """删除 Outcome 记录"""
    repo = OutcomeRepository(db)
    record = repo.get(outcome_id)
    if not record:
        raise HTTPException(status_code=404, detail="Outcome not found")
    repo.delete(record)
