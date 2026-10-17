"""flow_signal_cards 表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class FlowSignalCard(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "flow_signal_cards"

    asset: Mapped[str] = mapped_column(String(50), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # spot_vs_perp | funding_rate | open_interest | exchange_netflow
    # options_flow | large_tx | whale_accumulation | etf_flow

    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 信号内容
    direction: Mapped[str] = mapped_column(String(20), nullable=True)
    # bullish | bearish | neutral
    magnitude: Mapped[float] = mapped_column(Float, nullable=True)  # 信号强度 0~1
    raw_value: Mapped[float] = mapped_column(Float, nullable=True)
    normalized_value: Mapped[float] = mapped_column(Float, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)

    # 连续性追踪
    continuity_days: Mapped[int] = mapped_column(default=1, nullable=False)
    is_trend_change: Mapped[bool] = mapped_column(default=False, nullable=False)
    previous_signal_id: Mapped[str] = mapped_column(String(26), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    data_source: Mapped[str] = mapped_column(String(100), nullable=True)
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (
        Index("idx_flow_signal_asset", "asset", "signal_type", "observed_at"),
    )
