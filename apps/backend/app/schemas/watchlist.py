"""watchlists 和 watchlist_items 表"""
from sqlalchemy import String, Text, JSON, ForeignKey, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class Watchlist(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "watchlists"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    list_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # asset | theme | speaker | strategy
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    items = relationship("WatchlistItem", back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "watchlist_items"

    watchlist_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # asset | theme | speaker | strategy
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)  # BTC | 流动性 | 达利欧
    display_name: Mapped[str] = mapped_column(String(200), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    watchlist = relationship("Watchlist", back_populates="items")

    __table_args__ = (
        Index("idx_watchlist_items_watchlist_id", "watchlist_id"),
    )
