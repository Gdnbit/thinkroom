"""G-A2 — raw_sources 添加 article_summary 列

Revision ID: 0005_raw_source_article_summary
Revises: 0004_pgvector_embedding
Create Date: 2026-03-29

说明：
- 新增 article_summary TEXT 列，存储 LLM 生成的 150-300 字逻辑链摘要
- nullable，旧数据不受影响
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0005_raw_source_article_summary"
down_revision: Union[str, None] = "0004_pgvector_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raw_sources",
        sa.Column("article_summary", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("raw_sources", "article_summary")
