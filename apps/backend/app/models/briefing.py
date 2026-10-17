from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class HistoryMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str  # user question or assistant executive_summary


class BriefingRequest(BaseModel):
    question: str
    topic: Optional[str] = None
    asset_scope: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    time_horizon: Optional[str] = None
    committee_level: str = "single"  # single | double | triple
    history: List[HistoryMessage] = Field(default_factory=list)  # 多轮对话历史


class BriefingCardResponse(BaseModel):
    id: str
    title: str
    topic: str
    direction: str
    setup_quality: str
    time_horizon: Optional[str] = None
    executive_summary: str
    supporting_points: List[str] = Field(default_factory=list)
    contrary_points: List[str] = Field(default_factory=list)
    strategy_fit_summary: Optional[str] = None
    recommended_action: str
    invalidation_conditions: List[str] = Field(default_factory=list)
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
