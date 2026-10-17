"""G-A3 — 更新 claim_cards.embedding 列维度 1536->1024

Revision ID: 0006_embedding_dim_1024
Revises: 0005_raw_source_article_summary
Create Date: 2026-03-29

说明：
- 标准 postgres:16 不含 pgvector 扩展，此迁移为空操作
- 有 pgvector 环境时，手动执行：
    DROP INDEX IF EXISTS ix_claim_cards_embedding;
    ALTER TABLE claim_cards DROP COLUMN IF EXISTS embedding;
    ALTER TABLE claim_cards ADD COLUMN embedding vector(1024);
    CREATE INDEX ix_claim_cards_embedding ON claim_cards
      USING ivfflat (embedding vector_l2_ops) WITH (lists=100);
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0006_embedding_dim_1024"
down_revision: Union[str, None] = "0005_raw_source_article_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector 扩展不在标准 postgres:16 中，跳过
    # 有 pgvector 时手动执行上方注释中的 SQL
    pass


def downgrade() -> None:
    pass
