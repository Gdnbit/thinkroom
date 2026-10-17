"""job_runs 表 — 记录后台任务执行状态"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class JobRun(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "job_runs"

    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # ingestion | lifecycle_scan | event_fetch | market_update | flow_update | briefing_gen
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    # pending | running | completed | failed | cancelled

    triggered_by: Mapped[str] = mapped_column(String(50), default="system", nullable=False)
    input_params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    session_id: Mapped[str] = mapped_column(String(26), nullable=True)
    source_ids: Mapped[list] = mapped_column(JSON, nullable=True)
    object_scope: Mapped[dict] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)

    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    related_object_id: Mapped[str] = mapped_column(String(26), nullable=True)
    related_object_type: Mapped[str] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("idx_job_runs_status", "status"),
        Index("idx_job_runs_job_type", "job_type"),
        Index("idx_job_runs_session_id", "session_id"),
    )
