from pydantic import BaseModel
from typing import List, Optional


class UploadResponse(BaseModel):
    batch_id: str
    file_count: int
    source_ids: List[str] = []
    session_id: Optional[str] = None
    receipt_message_id: Optional[str] = None
    status: str  # queued | processing | completed | failed
    message: str
