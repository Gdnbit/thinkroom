"""setup_cards 表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class SetupCard(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "setup_cards"

    strategy_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_cards.id", ondelete="SET NULL"), nullable=True
    )
    asset: Mapped[str] = mapped_column(String(50), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # long | short | neutral

    # 评分
    strategy_fit_score: Mapped[float] = mapped_column(Float, nullable=True)  # 0~1
    evidence_strength_score: Mapped[float] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float] = mapped_column(Float, nullable=True)

    # 条件匹配
    matched_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    unmatched_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    disqualifying_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 建议
    recommended_action: Mapped[str] = mapped_column(String(50), nullable=True)
    # enter | watch | avoid | exit
    action_notes: Mapped[str] = mapped_column(Text, nullable=True)
    invalidation_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 支撑证据快照
    supporting_claim_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    supporting_event_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    market_state_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    flow_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # active | invalidated | expired | executed | archived
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    strategy = relationship("StrategyCard", back_populates="setups")
    briefings = relationship("BriefingCard", back_populates="setup")

    __table_args__ = (
        Index("idx_setup_cards_status", "status"),
        Index("idx_setup_cards_asset", "asset"),
    )
