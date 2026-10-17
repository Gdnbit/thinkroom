"""market_snapshots / fund profiles / fund estimate snapshots / source health logs"""
from datetime import datetime, date
from sqlalchemy import String, Float, DateTime, Date, JSON, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class MarketSnapshot(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "market_snapshots"

    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # US/HK/CN/CRYPTO/COMMODITY/FX
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    price: Mapped[float] = mapped_column(Float, nullable=True)
    pct_change_1d: Mapped[float] = mapped_column(Float, nullable=True)

    source_name: Mapped[str] = mapped_column(String(100), nullable=True)
    source_symbol: Mapped[str] = mapped_column(String(100), nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quality_flag: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)  # normal/stale/suspect

    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class FundProfile(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "fund_profiles"

    fund_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    fund_name: Mapped[str] = mapped_column(String(200), nullable=False)
    market: Mapped[str] = mapped_column(String(20), default="CN", nullable=False)

    # 估值基准信息
    last_nav: Mapped[float] = mapped_column(Float, nullable=True)
    last_nav_date: Mapped[date] = mapped_column(Date, nullable=True)

    # [{"symbol":"CSI300","weight":0.6}, ...]
    basket: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    beta: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    notes: Mapped[str] = mapped_column(String(500), nullable=True)


class FundEstimateSnapshot(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "fund_estimate_snapshots"

    fund_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    est_nav: Mapped[float] = mapped_column(Float, nullable=True)
    est_pct_change_1d: Mapped[float] = mapped_column(Float, nullable=True)
    confidence_level: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)

    ref_index: Mapped[str] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str] = mapped_column(String(30), default="v1", nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # G3: EOD 对账（actual_nav 由用户后填，是 ground truth；
    #     is_eod_final 由 scheduler 在每天收盘后标记当日最后一条估算）
    actual_nav: Mapped[float] = mapped_column(Float, nullable=True)
    is_eod_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index(
            "ix_fund_est_snap_eod_pending",
            "fund_code", "is_eod_final", "actual_nav",
        ),
    )


class SourceHealthLog(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "source_health_logs"

    source_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=True, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=True)
    http_status: Mapped[int] = mapped_column(nullable=True)
    error_message: Mapped[str] = mapped_column(String(500), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
