"""StrategyCard Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session
from app.services.base_repository import BaseRepository
from app.schemas.strategy_card import StrategyCard, StrategySourceLink


class StrategyRepository(BaseRepository[StrategyCard]):
    def __init__(self, db: Session):
        super().__init__(StrategyCard, db)

    def search(
        self,
        *,
        asset: Optional[str] = None,
        strategy_type: Optional[str] = None,
        status: str = "active",
        limit: int = 20,
        skip: int = 0,
    ) -> tuple[List[StrategyCard], int]:
        q = self.db.query(StrategyCard)
        if status:
            q = q.filter(StrategyCard.status == status)
        if strategy_type:
            q = q.filter(StrategyCard.strategy_type == strategy_type)
        if asset:
            q = q.filter(StrategyCard.applicable_assets.contains([asset]))
        total = q.count()
        items = q.order_by(StrategyCard.created_at.desc()).offset(skip).limit(limit).all()
        return items, total

    def get_by_asset(self, asset: str, limit: int = 10) -> List[StrategyCard]:
        return (
            self.db.query(StrategyCard)
            .filter(
                StrategyCard.status == "active",
                StrategyCard.applicable_assets.contains([asset]),
            )
            .order_by(StrategyCard.confidence.desc())
            .limit(limit)
            .all()
        )

    def get_source_links(self, strategy_id: str) -> List[StrategySourceLink]:
        return (
            self.db.query(StrategySourceLink)
            .filter(StrategySourceLink.strategy_id == strategy_id)
            .all()
        )

    def add_source_link(
        self, strategy_id: str, source_id: str, link_type: str = "distilled_from"
    ) -> StrategySourceLink:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        existing = (
            self.db.query(StrategySourceLink)
            .filter(
                StrategySourceLink.strategy_id == strategy_id,
                StrategySourceLink.source_id == source_id,
            )
            .first()
        )
        if existing:
            return existing
        link = StrategySourceLink(
            strategy_id=strategy_id,
            source_id=source_id,
            link_type=link_type,
            created_at=now,
            updated_at=now,
        )
        self.db.add(link)
        self.db.flush()
        return link
