"""decision_trace_eval_reviews 表 - trace-derived eval 候选人工审查状态"""

from sqlalchemy import JSON, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class DecisionTraceEvalReview(Base, ULIDPrimaryKey, TimestampMixin):
    """Persist human review state for generated trace eval candidates.

    This table does not promote a candidate into the main eval manifest. It only
    records reviewer intent so generated review queues stay stable across runs.
    """

    __tablename__ = "decision_trace_eval_reviews"
    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_decision_trace_eval_reviews_candidate"),
        Index("idx_decision_trace_eval_reviews_status_updated", "review_status", "updated_at"),
    )

    candidate_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending_review")
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    promoted_case_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    review_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
