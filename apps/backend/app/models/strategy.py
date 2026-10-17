from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class StrategyCardResponse(BaseModel):
    id: str
    strategy_name: str
    strategy_type: str
    summary: str
    core_logic: str
    applicable_assets: List[str] = []
    applicable_horizons: List[str] = []
    entry_conditions: List[str] = []
    disqualifying_conditions: List[str] = []
    risk_factors: List[str] = []
    confidence: Optional[float] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
