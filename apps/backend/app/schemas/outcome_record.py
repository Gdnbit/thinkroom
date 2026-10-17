"""outcome_records 表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class OutcomeRecord(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "outcome_records"

    briefing_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("briefing_cards.id", ondelete="SET NULL"), nullable=True
    )
    claim_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("claim_cards.id", ondelete="SET NULL"), nullable=True
    )
    setup_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("setup_cards.id", ondelete="SET NULL"), nullable=True
    )

    # 结果
    outcome_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # hit | miss | partial_hit | invalidated | not_triggered
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=True)
    actual_direction: Mapped[str] = mapped_column(String(20), nullable=True)
    predicted_direction: Mapped[str] = mapped_column(String(20), nullable=True)

    # 价格记录
    entry_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=True)

    # 时间
    prediction_made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    horizon_days: Mapped[int] = mapped_column(nullable=True)

    # 复盘
    review_notes: Mapped[str] = mapped_column(Text, nullable=True)
    what_went_right: Mapped[str] = mapped_column(Text, nullable=True)
    what_went_wrong: Mapped[str] = mapped_column(Text, nullable=True)
    lesson_learned: Mapped[str] = mapped_column(Text, nullable=True)
    logged_by: Mapped[str] = mapped_column(String(50), default="user", nullable=False)

    briefing = relationship("BriefingCard", back_populates="outcome")
