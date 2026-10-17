"""Add query indexes for decision trace events.

Revision ID: 0017_decision_trace_query_indexes
Revises: 0016_decision_snapshot_trigger_unique
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0017_decision_trace_query_indexes"
down_revision: Union[str, None] = "0016_decision_snapshot_trigger_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "idx_decision_trace_events_related_object_created",
        "decision_trace_events",
        ["related_object_type", "related_object_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_decision_trace_events_recent",
        "decision_trace_events",
        ["created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_decision_trace_events_recent", table_name="decision_trace_events")
    op.drop_index("idx_decision_trace_events_related_object_created", table_name="decision_trace_events")
