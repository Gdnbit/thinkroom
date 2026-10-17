"""Add explicit source links for decision trace events.

Revision ID: 0018_decision_trace_source_links
Revises: 0017_decision_trace_query_indexes
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_decision_trace_source_links"
down_revision: Union[str, None] = "0017_decision_trace_query_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_links() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        bind.execute(sa.text(
            """
            INSERT OR IGNORE INTO decision_trace_source_links (
                trace_event_id, source_id, created_at, updated_at
            )
            SELECT
                e.id,
                json_each.value,
                e.created_at,
                e.updated_at
            FROM decision_trace_events AS e, json_each(e.source_ids)
            WHERE json_valid(e.source_ids)
              AND json_each.value IS NOT NULL
              AND json_each.value != ''
            """
        ))
        return

    if dialect == "postgresql":
        bind.execute(sa.text(
            """
            INSERT INTO decision_trace_source_links (
                trace_event_id, source_id, created_at, updated_at
            )
            SELECT
                e.id,
                source.value,
                e.created_at,
                e.updated_at
            FROM decision_trace_events AS e
            CROSS JOIN LATERAL jsonb_array_elements_text(e.source_ids) AS source(value)
            WHERE source.value IS NOT NULL
              AND source.value != ''
            ON CONFLICT DO NOTHING
            """
        ))


def upgrade() -> None:
    op.create_table(
        "decision_trace_source_links",
        sa.Column("trace_event_id", sa.String(length=26), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trace_event_id"], ["decision_trace_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("trace_event_id", "source_id"),
    )
    op.create_index(
        "idx_decision_trace_source_links_source",
        "decision_trace_source_links",
        ["source_id", "trace_event_id"],
        unique=False,
    )
    _backfill_links()


def downgrade() -> None:
    op.drop_index(
        "idx_decision_trace_source_links_source",
        table_name="decision_trace_source_links",
    )
    op.drop_table("decision_trace_source_links")
