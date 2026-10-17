from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class EventCardResponse(BaseModel):
    id: str
    event_type: str
    title: str
    summary: Optional[str] = None
    source_name: str
    event_timestamp: datetime
    asset_scope: List[str] = []
    theme_scope: List[str] = []
    importance: float
    impact_direction: Optional[str] = None
    time_horizon: Optional[str] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
