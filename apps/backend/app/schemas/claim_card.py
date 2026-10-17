"""claim_cards 及关联表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, Boolean, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey

from app.config import settings

try:
    from pgvector.sqlalchemy import Vector
    _HAS_PGVECTOR = True
except ImportError:
    Vector = None
    _HAS_PGVECTOR = False

# 同时要求 Python 包和 DB 扩展都可用
_USE_VECTOR_COL = _HAS_PGVECTOR and settings.PGVECTOR_ENABLED


class ClaimCard(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "claim_cards"

    # 核心内容
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    full_quote: Mapped[str] = mapped_column(Text, nullable=True)
    # G17 Contextual Embeddings：LLM 抽取时同步生成的 50-100 字"局部上下文"
    # 用于在 embed 之前给 chunk 加来源/时间/发言人/主话题等定位信息
    # （Anthropic Contextual Retrieval, 2024-09，retrieval 失败率 −67%）
    # 老数据该列为 NULL；search 时退化为 summary+full_quote 的旧路径，完全兼容
    contextual_text: Mapped[str] = mapped_column(Text, nullable=True)
    claim_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # macro_view | sector_view | asset_view | strategy_rule | risk_warning | data_observation | trading_pattern | market_narrative
    stance: Mapped[str] = mapped_column(String(20), nullable=False)
    # bullish | bearish | neutral | conditional
    time_horizon: Mapped[str] = mapped_column(String(20), nullable=True)
    # immediate | short | medium | long | unspecified

    # 范围
    asset_scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    theme_scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    geographic_scope: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 条件与逻辑
    conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    invalidation_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reasoning_basis: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # 质量指标
    confidence: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)
    source_trust_weight: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    is_contradicted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contradiction_notes: Mapped[str] = mapped_column(Text, nullable=True)

    # 生命周期
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    # active | expired | invalidated | superseded | archived
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    time_sensitive: Mapped[bool] = mapped_column(default=True, nullable=False)
    # False = 长期有效（理念/框架类观点），True = 有时效性（交易/事件类观点）
    expiry_hint: Mapped[str] = mapped_column(String(200), nullable=True)
    # 自然语言失效描述，如 "三天后"、"FOMC 决议后"
    last_lifecycle_check: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    expiry_reason: Mapped[str] = mapped_column(Text, nullable=True)

    # 来源
    speaker_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("speaker_profiles.id", ondelete="SET NULL"), nullable=True
    )

    # 版本
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    parent_claim_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("claim_cards.id", ondelete="SET NULL"), nullable=True
    )

    # 语义搜索向量：pgvector 可用时用 Vector 列，否则用 TEXT + JSON 序列化
    if _USE_VECTOR_COL:
        embedding: Mapped[list] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)
    else:
        from sqlalchemy import Column, Text as _Text
        embedding: Mapped[str] = mapped_column(_Text, nullable=True)

    # 关系
    speaker = relationship("SpeakerProfile", back_populates="claims")
    raw_source_links = relationship("ClaimRawSourceLink", back_populates="claim", cascade="all, delete-orphan")
    evidence_links = relationship("ClaimEvidenceLink", back_populates="claim", cascade="all, delete-orphan")
    lifecycle_logs = relationship("LifecycleLog", back_populates="claim", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_claim_cards_status", "status"),
        Index("idx_claim_cards_speaker_id", "speaker_id"),
        Index("idx_claim_cards_created_at", "created_at"),
        Index("idx_claim_cards_valid_until", "valid_until"),
        Index("idx_claim_cards_asset_scope", "asset_scope", postgresql_using="gin"),
        Index("idx_claim_cards_theme_scope", "theme_scope", postgresql_using="gin"),
    )


class ClaimRawSourceLink(Base, TimestampMixin):
    __tablename__ = "claim_raw_source_links"

    claim_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("claim_cards.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("raw_sources.id", ondelete="CASCADE"), primary_key=True
    )
    link_type: Mapped[str] = mapped_column(String(50), default="extracted_from", nullable=False)

    claim = relationship("ClaimCard", back_populates="raw_source_links")
    source = relationship("RawSource")


class ClaimEvidenceLink(Base, TimestampMixin):
    __tablename__ = "claim_evidence_links"

    claim_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("claim_cards.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_span_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("evidence_spans.id", ondelete="CASCADE"), primary_key=True
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    claim = relationship("ClaimCard", back_populates="evidence_links")
    evidence_span = relationship("EvidenceSpan")
