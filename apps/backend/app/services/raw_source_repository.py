"""RawSource Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session
from app.services.base_repository import BaseRepository
from app.schemas.raw_source import RawSource


class RawSourceRepository(BaseRepository[RawSource]):
    def __init__(self, db: Session):
        super().__init__(RawSource, db)

    def get_pending(self, limit: int = 10) -> List[RawSource]:
        return (
            self.db.query(RawSource)
            .filter(RawSource.status == "pending")
            .order_by(RawSource.created_at.asc())
            .limit(limit)
            .all()
        )

    def get_by_status(self, status: str, limit: int = 50) -> List[RawSource]:
        return (
            self.db.query(RawSource)
            .filter(RawSource.status == status)
            .order_by(RawSource.created_at.desc())
            .limit(limit)
            .all()
        )

    def mark_processing(self, source: RawSource) -> RawSource:
        from datetime import datetime, timezone
        return self.update(source, {
            "status": "processing",
            "ingested_at": datetime.now(timezone.utc),
        })

    def mark_completed(self, source: RawSource) -> RawSource:
        return self.update(source, {"status": "completed"})

    def mark_failed(self, source: RawSource, error: str) -> RawSource:
        return self.update(source, {"status": "failed", "processing_error": error})
