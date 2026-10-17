"""G4 — briefing_evidence_links 关联表

Revision ID: 0003_briefing_evidence_links
Revises: 0002_initial_p2
Create Date: 2026-03-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_briefing_evidence_links"
down_revision: Union[str, None] = "0002_initial_p2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "briefing_evidence_links",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("briefing_id", sa.String(26), nullable=False, index=True),
        sa.Column(
            "evidence_type",
            sa.String(20),
            nullable=False,
            comment="claim | event | market | flow",
        ),
        sa.Column("evidence_id", sa.String(26), nullable=True),
        sa.Column(
            "role",
            sa.String(20),
            nullable=False,
            default="supporting",
            comment="supporting | contrary | background",
        ),
        sa.Column("relevance_score", sa.Float, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["briefing_id"],
            ["briefing_cards.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_briefing_evidence_links_type_id",
        "briefing_evidence_links",
        ["evidence_type", "evidence_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_briefing_evidence_links_type_id", table_name="briefing_evidence_links")
    op.drop_table("briefing_evidence_links")
