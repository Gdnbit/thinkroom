"""BriefingCard Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session
from app.services.base_repository import BaseRepository
from app.schemas.briefing_card import BriefingCard


class BriefingRepository(BaseRepository[BriefingCard]):
    def __init__(self, db: Session):
        super().__init__(BriefingCard, db)

    def get_recent(self, limit: int = 20) -> List[BriefingCard]:
        return (
            self.db.query(BriefingCard)
            .filter(BriefingCard.status == "active")
            .order_by(BriefingCard.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_asset(self, asset: str, limit: int = 10) -> List[BriefingCard]:
        return (
            self.db.query(BriefingCard)
            .filter(
                BriefingCard.status == "active",
                BriefingCard.asset_scope.contains([asset]),
            )
            .order_by(BriefingCard.created_at.desc())
            .limit(limit)
            .all()
        )
