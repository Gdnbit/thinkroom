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
    # Preserve revision identities shared with existing installations. PostgreSQL
    # enforces Alembic's default VARCHAR(32), but this revision is 37 characters.
    # Widen before Alembic records it; never shrink again on downgrade.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        version_column = next(
            column for column in sa.inspect(bind).get_columns("alembic_version")
            if column["name"] == "version_num"
        )
        length = getattr(version_column["type"], "length", None)
        if length is not None and length < 128:
            op.alter_column(
                "alembic_version", "version_num",
                existing_type=version_column["type"], type_=sa.String(128),
                existing_nullable=False,
            )
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
