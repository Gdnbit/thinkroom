"""briefing_cards 表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class BriefingCard(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "briefing_cards"

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=True)  # 触发问题

    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # bullish | bearish | neutral
    setup_quality: Mapped[str] = mapped_column(String(20), nullable=False)
    # high | medium | low | insufficient
    time_horizon: Mapped[str] = mapped_column(String(20), nullable=True)
    asset_scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 内容
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_points: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    contrary_points: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    key_risks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    strategy_fit_summary: Mapped[str] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str] = mapped_column(String(50), nullable=False)
    # enter | watch | avoid | exit | hold
    invalidation_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 溯源
    setup_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("setup_cards.id", ondelete="SET NULL"), nullable=True
    )
    source_claim_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_event_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 生成元数据
    model_used: Mapped[str] = mapped_column(String(100), nullable=True)
    committee_level: Mapped[str] = mapped_column(String(20), default="single", nullable=False)
    generation_time_ms: Mapped[int] = mapped_column(nullable=True)
    token_count: Mapped[int] = mapped_column(nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    setup = relationship("SetupCard", back_populates="briefings")
    outcome = relationship("OutcomeRecord", back_populates="briefing", uselist=False)

    __table_args__ = (
        Index("idx_briefing_cards_created_at", "created_at"),
        Index("idx_briefing_cards_asset_scope", "asset_scope", postgresql_using="gin"),
    )
