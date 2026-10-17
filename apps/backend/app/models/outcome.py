from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class OutcomeCreateRequest(BaseModel):
    """创建 Outcome 记录的请求体"""
    briefing_id: Optional[str] = None
    claim_id: Optional[str] = None
    setup_id: Optional[str] = None

    outcome_type: str  # hit | miss | partial_hit | invalidated | not_triggered
    is_correct: Optional[bool] = None
    actual_direction: Optional[str] = None
    predicted_direction: Optional[str] = None

    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None

    prediction_made_at: Optional[datetime] = None
    outcome_observed_at: Optional[datetime] = None
    horizon_days: Optional[int] = None

    review_notes: Optional[str] = None
    what_went_right: Optional[str] = None
    what_went_wrong: Optional[str] = None
    lesson_learned: Optional[str] = None
    logged_by: str = "user"


class OutcomeUpdateRequest(BaseModel):
    """更新 Outcome 记录的请求体（所有字段可选）"""
    outcome_type: Optional[str] = None
    is_correct: Optional[bool] = None
    actual_direction: Optional[str] = None
    predicted_direction: Optional[str] = None

    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None

    prediction_made_at: Optional[datetime] = None
    outcome_observed_at: Optional[datetime] = None
    horizon_days: Optional[int] = None

    review_notes: Optional[str] = None
    what_went_right: Optional[str] = None
    what_went_wrong: Optional[str] = None
    lesson_learned: Optional[str] = None


class OutcomeResponse(BaseModel):
    id: str
    briefing_id: Optional[str] = None
    claim_id: Optional[str] = None
    setup_id: Optional[str] = None

    outcome_type: str
    is_correct: Optional[bool] = None
    actual_direction: Optional[str] = None
    predicted_direction: Optional[str] = None

    entry_price: Optional[float] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None

    prediction_made_at: Optional[datetime] = None
    outcome_observed_at: Optional[datetime] = None
    horizon_days: Optional[int] = None

    review_notes: Optional[str] = None
    what_went_right: Optional[str] = None
    what_went_wrong: Optional[str] = None
    lesson_learned: Optional[str] = None
    logged_by: str

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OutcomeListResponse(BaseModel):
    outcomes: list[OutcomeResponse]
    total: int
    has_more: bool


class OutcomeStatsResponse(BaseModel):
    total: int
    hit_count: int
    miss_count: int
    partial_hit_count: int
    not_triggered_count: int
    invalidated_count: int
    hit_rate: float          # hit / (hit + miss + partial_hit)
    correct_rate: float      # is_correct=True / total with is_correct set
    avg_pnl_pct: Optional[float] = None
