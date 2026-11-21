"""Index claim links for source-first evidence lookups."""

from alembic import op


revision = "0022_claim_source_lookup"
down_revision = "0021_job_run_session_index"
branch_labels = None
depends_on = None

INDEX = "ix_claim_raw_source_links_source_id"
TABLE = "claim_raw_source_links"


def upgrade() -> None:
    op.create_index(INDEX, TABLE, ["source_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE, if_exists=True)
