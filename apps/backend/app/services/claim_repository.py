"""ClaimCard Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.services.base_repository import BaseRepository
from app.schemas.claim_card import ClaimCard


class ClaimRepository(BaseRepository[ClaimCard]):
    def __init__(self, db: Session):
        super().__init__(ClaimCard, db)

    def search(
        self,
        *,
        q: Optional[str] = None,
        asset: Optional[str] = None,
        theme: Optional[str] = None,
        status: Optional[str] = "active",
        speaker_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[List[ClaimCard], int]:
        query = self.db.query(ClaimCard)

        if status:
            query = query.filter(ClaimCard.status == status)
        if speaker_id:
            query = query.filter(ClaimCard.speaker_id == speaker_id)
        if q:
            query = query.filter(
                or_(
                    ClaimCard.summary.ilike(f"%{q}%"),
                    ClaimCard.full_quote.ilike(f"%{q}%"),
                )
            )

        # 先取全部（不 limit），在 Python 端过滤 JSON 数组字段
        all_items = query.order_by(ClaimCard.created_at.desc()).all()

        if asset:
            all_items = [c for c in all_items if asset in (c.asset_scope or [])]
        if theme:
            all_items = [c for c in all_items if theme in (c.theme_scope or [])]

        total = len(all_items)
        items = all_items[skip: skip + limit]
        return items, total

    def get_active_by_asset(self, asset: str) -> List[ClaimCard]:
        all_active = (
            self.db.query(ClaimCard)
            .filter(ClaimCard.status == "active")
            .order_by(ClaimCard.created_at.desc())
            .all()
        )
        return [c for c in all_active if asset in (c.asset_scope or [])]

    def get_expiring_soon(self, days: int = 3) -> List[ClaimCard]:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) + timedelta(days=days)
        return (
            self.db.query(ClaimCard)
            .filter(
                ClaimCard.status == "active",
                ClaimCard.valid_until <= cutoff,
            )
            .all()
        )
