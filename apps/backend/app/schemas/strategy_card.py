"""strategy_cards 及关联表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class StrategyCard(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "strategy_cards"

    strategy_name: Mapped[str] = mapped_column(String(300), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # trend_following | mean_reversion | breakout | event_driven
    # macro_rotation | carry | arbitrage | custom

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    core_logic: Mapped[str] = mapped_column(Text, nullable=True)

    applicable_assets: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    applicable_horizons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    applicable_regimes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    entry_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    disqualifying_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    exit_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    risk_factors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    position_sizing_notes: Mapped[str] = mapped_column(Text, nullable=True)

    historical_win_rate: Mapped[float] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    last_validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    distilled_by: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    # manual | strategy_distillation_skill

    source_links = relationship("StrategySourceLink", back_populates="strategy", cascade="all, delete-orphan")
    setups = relationship("SetupCard", back_populates="strategy")


class StrategySourceLink(Base, TimestampMixin):
    __tablename__ = "strategy_source_links"

    strategy_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("strategy_cards.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("raw_sources.id", ondelete="CASCADE"), primary_key=True
    )
    link_type: Mapped[str] = mapped_column(String(50), default="distilled_from", nullable=False)

    strategy = relationship("StrategyCard", back_populates="source_links")
    source = relationship("RawSource")
