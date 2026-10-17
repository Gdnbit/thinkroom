"""raw_sources 与 source_attachments 表"""
from sqlalchemy import Column, String, Text, Float, DateTime, Integer, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class RawSource(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "raw_sources"

    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # article|video|tweet|report|note|chat|image
    title: Mapped[str] = mapped_column(String(500), nullable=True)
    original_url: Mapped[str] = mapped_column(String(2000), nullable=True)
    publisher: Mapped[str] = mapped_column(String(200), nullable=True)
    published_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="zh", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    # pending | processing | completed | failed
    processing_error: Mapped[str] = mapped_column(Text, nullable=True)
    speaker_hint: Mapped[str] = mapped_column(String(200), nullable=True)
    theme_hint: Mapped[str] = mapped_column(String(200), nullable=True)
    ingested_at: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=True)
    article_summary: Mapped[str] = mapped_column(Text, nullable=True)  # LLM 生成的逻辑链摘要

    # 增量更新/去重
    content_hash: Mapped[str] = mapped_column(String(64), nullable=True, unique=True, index=True)
    # SHA-256 of original file content, used to detect duplicate uploads

    # 用户主观标注（上传时可选填写）
    user_trust_level: Mapped[str] = mapped_column(String(20), nullable=True)
    # high | medium | low — 用户对来源的信任程度
    collection_reason: Mapped[str] = mapped_column(String(50), nullable=True)
    # opinion_quality | logic_chain | research_value | trading_pattern | market_narrative | reference
    user_note: Mapped[str] = mapped_column(Text, nullable=True)
    # 用户对该资料的自由备注（为什么觉得有价值）

    __table_args__ = (
        Index("idx_raw_sources_status", "status"),
        Index("idx_raw_sources_created_at", "created_at"),
    )

    # 关系
    attachments = relationship("SourceAttachment", back_populates="source", cascade="all, delete-orphan")


class SourceAttachment(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "source_attachments"

    source_id: Mapped[str] = mapped_column(String(26), ForeignKey("raw_sources.id", ondelete="CASCADE"), nullable=False)
    attachment_type: Mapped[str] = mapped_column(String(50), nullable=False)  # image|pdf|audio
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ocr_text: Mapped[str] = mapped_column(Text, nullable=True)
    transcription_text: Mapped[str] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_source_attachments_source_id", "source_id"),
    )

    # 关系
    source = relationship("RawSource", back_populates="attachments")
