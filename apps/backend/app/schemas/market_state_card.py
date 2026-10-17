"""market_state_cards 表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class MarketStateCard(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "market_state_cards"

    asset: Mapped[str] = mapped_column(String(50), nullable=False)  # BTC / SPX / GOLD
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)  # 1d / 4h / 1w
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 价格
    price: Mapped[float] = mapped_column(Float, nullable=True)
    price_change_1d: Mapped[float] = mapped_column(Float, nullable=True)
    price_change_7d: Mapped[float] = mapped_column(Float, nullable=True)
    price_change_30d: Mapped[float] = mapped_column(Float, nullable=True)

    # 技术状态
    trend_direction: Mapped[str] = mapped_column(String(20), nullable=True)
    # uptrend | downtrend | sideways | breakout | breakdown
    trend_strength: Mapped[float] = mapped_column(Float, nullable=True)  # 0~1
    key_support_levels: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    key_resistance_levels: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 技术指标快照
    rsi_14: Mapped[float] = mapped_column(Float, nullable=True)
    ma_20: Mapped[float] = mapped_column(Float, nullable=True)
    ma_50: Mapped[float] = mapped_column(Float, nullable=True)
    ma_200: Mapped[float] = mapped_column(Float, nullable=True)
    volume_vs_avg: Mapped[float] = mapped_column(Float, nullable=True)  # 当前量/均量
    atr_14: Mapped[float] = mapped_column(Float, nullable=True)

    # 市场结构
    structure_notes: Mapped[str] = mapped_column(Text, nullable=True)
    volatility_state: Mapped[str] = mapped_column(String(20), nullable=True)
    # low | normal | elevated | extreme

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    data_source: Mapped[str] = mapped_column(String(100), nullable=True)
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (
        Index("idx_market_state_asset_time", "asset", "timeframe", "snapshot_at"),
    )
