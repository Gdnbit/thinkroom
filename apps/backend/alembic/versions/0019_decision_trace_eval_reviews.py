"""Add persisted review state for trace eval candidates.

Revision ID: 0019_decision_trace_eval_reviews
Revises: 0018_decision_trace_source_links
Create Date: 2026-07-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_decision_trace_eval_reviews"
down_revision: Union[str, None] = "0018_decision_trace_source_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_trace_eval_reviews",
        sa.Column("candidate_id", sa.String(length=160), nullable=False),
        sa.Column("review_status", sa.String(length=40), nullable=False),
        sa.Column("reviewer", sa.String(length=100), nullable=True),
        sa.Column("decision_note", sa.String(length=1000), nullable=True),
        sa.Column("promoted_case_id", sa.String(length=160), nullable=True),
        sa.Column("review_metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", name="uq_decision_trace_eval_reviews_candidate"),
    )
    op.create_index(
        op.f("ix_decision_trace_eval_reviews_candidate_id"),
        "decision_trace_eval_reviews",
        ["candidate_id"],
        unique=False,
    )
    op.create_index(
        "idx_decision_trace_eval_reviews_status_updated",
        "decision_trace_eval_reviews",
        ["review_status", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_decision_trace_eval_reviews_status_updated",
        table_name="decision_trace_eval_reviews",
    )
    op.drop_index(
        op.f("ix_decision_trace_eval_reviews_candidate_id"),
        table_name="decision_trace_eval_reviews",
    )
    op.drop_table("decision_trace_eval_reviews")
