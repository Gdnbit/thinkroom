"""EventCard Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session
from app.services.base_repository import BaseRepository
from app.schemas.event_card import EventCard


class EventRepository(BaseRepository[EventCard]):
    def __init__(self, db: Session):
        super().__init__(EventCard, db)

    def get_recent(
        self,
        *,
        asset: Optional[str] = None,
        theme: Optional[str] = None,
        importance_min: float = 0.0,
        limit: int = 20,
    ) -> List[EventCard]:
        query = (
            self.db.query(EventCard)
            .filter(
                EventCard.status == "active",
                EventCard.importance >= importance_min,
            )
        )
        if asset:
            query = query.filter(EventCard.asset_scope.contains([asset]))
        if theme:
            query = query.filter(EventCard.theme_scope.contains([theme]))
        return query.order_by(EventCard.event_timestamp.desc()).limit(limit).all()
