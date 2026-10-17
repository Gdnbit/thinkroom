"""lifecycle_logs 表"""
from datetime import datetime
from sqlalchemy import String, Text, DateTime, JSON, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class LifecycleLog(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "lifecycle_logs"

    claim_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("claim_cards.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(100), nullable=True)
    # system | user | lifecycle_skill
    evidence_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    claim = relationship("ClaimCard", back_populates="lifecycle_logs")

    __table_args__ = (
        Index("idx_lifecycle_logs_claim_id", "claim_id"),
    )
