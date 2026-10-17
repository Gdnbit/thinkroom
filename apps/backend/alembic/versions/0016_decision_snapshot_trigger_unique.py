"""Add unique trigger constraint for decision snapshots.

Revision ID: 0016_decision_snapshot_trigger_unique
Revises: 0015_decision_trace_events
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_decision_snapshot_trigger_unique"
down_revision: Union[str, None] = "0015_decision_trace_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_decision_snapshots_trigger"


def _duplicate_trigger_objects() -> list[tuple[str, str, int]]:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        """
        SELECT trigger_type, trigger_id, COUNT(*) AS duplicate_count
        FROM decision_snapshots
        GROUP BY trigger_type, trigger_id
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC, trigger_type, trigger_id
        LIMIT 10
        """
    )).fetchall()
    return [
        (
            str(row._mapping["trigger_type"]),
            str(row._mapping["trigger_id"]),
            int(row._mapping["duplicate_count"]),
        )
        for row in rows
    ]


def _raise_duplicate_error(duplicates: list[tuple[str, str, int]]) -> None:
    details = "; ".join(
        f"{trigger_type}:{trigger_id}={count}"
        for trigger_type, trigger_id, count in duplicates
    )
    raise RuntimeError(
        "Cannot add uq_decision_snapshots_trigger because duplicate "
        "(trigger_type, trigger_id) rows already exist. Preserve the data, "
        "archive or reconcile the duplicate snapshots manually, then rerun "
        f"the migration. Sample duplicates: {details}"
    )


def upgrade() -> None:
    duplicates = _duplicate_trigger_objects()
    if duplicates:
        _raise_duplicate_error(duplicates)

    op.create_index(
        INDEX_NAME,
        "decision_snapshots",
        ["trigger_type", "trigger_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="decision_snapshots")
