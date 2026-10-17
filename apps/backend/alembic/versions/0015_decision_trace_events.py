"""P1 - add decision_trace_events table

Revision ID: 0015_decision_trace_events
Revises: 0014_claim_contextual_text
Create Date: 2026-06-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015_decision_trace_events"
down_revision: Union[str, None] = "0014_claim_contextual_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "decision_trace_events",
        sa.Column("related_object_type", sa.String(length=50), nullable=False),
        sa.Column("related_object_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.String(length=26), nullable=True),
        sa.Column("job_id", sa.String(length=26), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("step_name", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=150), nullable=True),
        sa.Column("skill_role", sa.String(length=100), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("source_ids", json_type, nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_decision_trace_events_event_type"), "decision_trace_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_decision_trace_events_job_id"), "decision_trace_events", ["job_id"], unique=False)
    op.create_index(op.f("ix_decision_trace_events_related_object_id"), "decision_trace_events", ["related_object_id"], unique=False)
    op.create_index(op.f("ix_decision_trace_events_related_object_type"), "decision_trace_events", ["related_object_type"], unique=False)
    op.create_index(op.f("ix_decision_trace_events_run_id"), "decision_trace_events", ["run_id"], unique=False)
    op.create_index(op.f("ix_decision_trace_events_session_id"), "decision_trace_events", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_decision_trace_events_session_id"), table_name="decision_trace_events")
    op.drop_index(op.f("ix_decision_trace_events_run_id"), table_name="decision_trace_events")
    op.drop_index(op.f("ix_decision_trace_events_related_object_type"), table_name="decision_trace_events")
    op.drop_index(op.f("ix_decision_trace_events_related_object_id"), table_name="decision_trace_events")
    op.drop_index(op.f("ix_decision_trace_events_job_id"), table_name="decision_trace_events")
    op.drop_index(op.f("ix_decision_trace_events_event_type"), table_name="decision_trace_events")
    op.drop_table("decision_trace_events")
