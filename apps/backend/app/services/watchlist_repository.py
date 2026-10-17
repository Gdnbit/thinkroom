"""Watchlist Repository"""
from typing import List
from sqlalchemy.orm import Session
from app.services.base_repository import BaseRepository
from app.schemas.watchlist import Watchlist, WatchlistItem


class WatchlistRepository(BaseRepository[Watchlist]):
    def __init__(self, db: Session):
        super().__init__(Watchlist, db)

    def get_active(self) -> List[Watchlist]:
        return (
            self.db.query(Watchlist)
            .filter(Watchlist.status == "active")
            .order_by(Watchlist.created_at.desc())
            .all()
        )

    def get_with_items(self, watchlist_id: str) -> Watchlist | None:
        return (
            self.db.query(Watchlist)
            .filter(Watchlist.id == watchlist_id)
            .first()
        )


class WatchlistItemRepository(BaseRepository[WatchlistItem]):
    def __init__(self, db: Session):
        super().__init__(WatchlistItem, db)

    def get_by_watchlist(self, watchlist_id: str) -> List[WatchlistItem]:
        return (
            self.db.query(WatchlistItem)
            .filter(
                WatchlistItem.watchlist_id == watchlist_id,
                WatchlistItem.status == "active",
            )
            .order_by(WatchlistItem.priority.desc())
            .all()
        )
