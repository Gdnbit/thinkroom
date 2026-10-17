"""G11 — pgvector 语义搜索：claim_cards 添加 embedding 列

Revision ID: 0004_pgvector_embedding
Revises: 0003_briefing_evidence_links
Create Date: 2026-03-23

说明：
- 标准 postgres:16 不含 pgvector 扩展，此迁移为空操作
- 有 pgvector 环境时，手动执行：
    CREATE EXTENSION vector;
    ALTER TABLE claim_cards ADD COLUMN embedding vector(1536);
    CREATE INDEX ix_claim_cards_embedding ON claim_cards
      USING ivfflat (embedding vector_l2_ops) WITH (lists=100);
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004_pgvector_embedding"
down_revision: Union[str, None] = "0003_briefing_evidence_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector 扩展不在标准 postgres:16 中，跳过
    # 当 PostgreSQL 已安装 pgvector 时，手动执行上方注释中的 SQL
    pass


def downgrade() -> None:
    pass
