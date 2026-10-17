"""event_cards 表

V2 升级：新增真假状态轴（truth_status）和兑现状态轴（outcome_status），
将事件从简单文本记录升级为带状态的事件对象。

状态模型：
  truth_status:   unverified → partially_verified → confirmed / denied / retracted
  outcome_status: pending → no_follow_through / partially_materialized / fully_materialized / opposite_outcome
"""
from datetime import datetime
from sqlalchemy import String, Text, Float, Boolean, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class EventCard(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "event_cards"

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # macro | earnings | regulatory | geopolitical | technical_break | central_bank | custom
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=True)

    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    asset_scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    theme_scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    geographic_scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)  # 0~1
    impact_direction: Mapped[str] = mapped_column(String(20), nullable=True)
    # bullish | bearish | neutral | mixed
    time_horizon: Mapped[str] = mapped_column(String(20), nullable=True)
    is_surprise: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    market_reaction_notes: Mapped[str] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    raw_source_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("raw_sources.id", ondelete="SET NULL"), nullable=True
    )
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # ── V2：真假状态轴 ──────────────────────────────────────────
    truth_status: Mapped[str] = mapped_column(
        String(30), default="unverified", nullable=False
    )
    # unverified | partially_verified | confirmed | denied | retracted
    truth_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    truth_source: Mapped[str] = mapped_column(String(500), nullable=True)

    # ── V2：兑现状态轴 ──────────────────────────────────────────
    outcome_status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False
    )
    # pending | no_follow_through | partially_materialized | fully_materialized | opposite_outcome
    outcome_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_notes: Mapped[str] = mapped_column(Text, nullable=True)

    # ── V2：信息质量与追踪 ──────────────────────────────────────
    source_tier: Mapped[str] = mapped_column(
        String(30), default="tier3_kol", nullable=False
    )
    # tier1_official | tier2_major_media | tier3_kol | tier4_rumor
    sentiment: Mapped[float] = mapped_column(Float, nullable=True)       # -1.0 ~ 1.0
    novelty: Mapped[float] = mapped_column(Float, nullable=True)         # 0.0 ~ 1.0
    trust_weight: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=True)         # 核心断言文本
    invalidation_window: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    followup_event_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # ── V2：用户修正 ────────────────────────────────────────────
    user_override_truth: Mapped[str] = mapped_column(String(30), nullable=True)
    user_override_outcome: Mapped[str] = mapped_column(String(30), nullable=True)
    user_override_notes: Mapped[str] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_event_cards_timestamp", "event_timestamp"),
        Index("idx_event_cards_importance", "importance"),
        Index("idx_event_cards_asset_scope", "asset_scope", postgresql_using="gin"),
    )
