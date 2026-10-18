"""Align the durable job session lookup index with ORM metadata.

Preserve all existing revision identities and accept legacy create_all indexes.
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_job_run_session_index"
down_revision = "0020_job_run_scope_columns"
branch_labels = None
depends_on = None

INDEX = "idx_job_runs_session_id"


def _indexes():
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("job_runs")}


def upgrade():
    # Session-scoped durable jobs are a persisted lookup boundary. Keep the
    # non-unique index promised by JobRun instead of dropping metadata to hide drift.
    if INDEX not in _indexes():
        op.create_index(INDEX, "job_runs", ["session_id"], unique=False)


def downgrade():
    if INDEX in _indexes():
        op.drop_index(INDEX, table_name="job_runs")
