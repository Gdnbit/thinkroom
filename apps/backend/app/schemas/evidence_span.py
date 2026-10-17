"""evidence_spans 表"""
from sqlalchemy import String, Text, Float, Integer, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class EvidenceSpan(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "evidence_spans"

    source_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("raw_sources.id", ondelete="CASCADE"), nullable=False
    )
    span_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # quote | summary | data_point | chart_ref
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_or_timestamp: Mapped[str] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(50), default="llm", nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    # 关系
    source = relationship("RawSource")

    __table_args__ = (
        Index("idx_evidence_spans_source_id", "source_id"),
    )
