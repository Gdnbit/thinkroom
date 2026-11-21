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

        ordered = query.order_by(ClaimCard.created_at.desc(), ClaimCard.id.desc())
        if not asset and not theme:
            return ordered.offset(skip).limit(limit).all(), query.count()

        # Preserve exact JSON membership across databases and legacy rows.
        # Scan lightweight candidates and load full records only for the page.
        candidates = ordered.with_entities(ClaimCard.id, ClaimCard.asset_scope, ClaimCard.theme_scope)
        total = 0
        page_ids = []
        for row in candidates.yield_per(500):
            if asset and asset not in (row.asset_scope or []):
                continue
            if theme and theme not in (row.theme_scope or []):
                continue
            if skip <= total < skip + limit:
                page_ids.append(row.id)
            total += 1
        page = ordered.filter(ClaimCard.id.in_(page_ids)).all() if page_ids else []
        return page, total

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
