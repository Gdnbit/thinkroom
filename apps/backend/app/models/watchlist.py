from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


class WatchlistCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    list_type: str  # asset | theme | speaker | strategy
    items: List[str] = []


class WatchlistResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    list_type: str
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
