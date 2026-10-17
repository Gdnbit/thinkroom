"""decision_trace_events 表 - 决策/问答运行级可回溯事件"""

from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class DecisionTraceEvent(Base, ULIDPrimaryKey, TimestampMixin):
    """记录 chat / briefing / committee 的范围、质量、护栏与 LLM 调用事件。

    与 processing_traces 不同，这张表面向决策输出本身，而不是单个资料源
    的解析流水线。事件采用 append-only 形态，方便后续映射到 OpenTelemetry
    span、Langfuse observation 或内部审计页面。
    """

    __tablename__ = "decision_trace_events"
    __table_args__ = (
        Index(
            "idx_decision_trace_events_related_object_created",
            "related_object_type",
            "related_object_id",
            "created_at",
            "id",
        ),
        Index("idx_decision_trace_events_recent", "created_at", "id"),
    )

    related_object_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    related_object_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    step_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)

    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    skill_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    source_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    source_links = relationship(
        "DecisionTraceSourceLink",
        back_populates="trace_event",
        cascade="all, delete-orphan",
    )


class DecisionTraceSourceLink(Base, TimestampMixin):
    __tablename__ = "decision_trace_source_links"

    trace_event_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("decision_trace_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    trace_event = relationship("DecisionTraceEvent", back_populates="source_links")

    __table_args__ = (
        Index("idx_decision_trace_source_links_source", "source_id", "trace_event_id"),
    )
