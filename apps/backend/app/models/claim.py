from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class ClaimCardResponse(BaseModel):
    id: str
    summary: str
    claim_type: str
    stance: str
    time_horizon: Optional[str] = None
    status: str
    confidence: float
    asset_scope: List[str] = []
    theme_scope: List[str] = []
    reasoning_basis: List[str] = []
    speaker_id: Optional[str] = None
    speaker_name: Optional[str] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ClaimSearchResponse(BaseModel):
    claims: List[ClaimCardResponse]
    total: int
    has_more: bool
