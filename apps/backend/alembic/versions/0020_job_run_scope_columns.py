"""Add persisted source/session scope to job_runs.

Revision ID: 0020_job_run_scope_columns
Revises: 0019_decision_trace_eval_reviews
Create Date: 2026-07-12

Development historically repaired these nullable columns at startup. Production
startup is deliberately read-only, so the canonical Alembic chain must contain
them. The conditional adds also support databases already repaired by the old
development compatibility path.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_job_run_scope_columns"
down_revision: Union[str, None] = "0019_decision_trace_eval_reviews"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    sa.Column("session_id", sa.String(length=26), nullable=True),
    sa.Column("source_ids", sa.JSON(), nullable=True),
    sa.Column("object_scope", sa.JSON(), nullable=True),
)


def _has_column(name: str) -> bool:
    bind = op.get_bind()
    return name in {column["name"] for column in sa.inspect(bind).get_columns("job_runs")}


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("job_runs"):
        return
    for column in _COLUMNS:
        if not _has_column(column.name):
            op.add_column("job_runs", column)


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("job_runs"):
        return
    for column in reversed(_COLUMNS):
        if _has_column(column.name):
            op.drop_column("job_runs", column.name)
