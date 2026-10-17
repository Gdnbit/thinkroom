"""G17 — claim_cards 添加 contextual_text 列（Contextual Embeddings）

Revision ID: 0014_claim_contextual_text
Revises: 0013_g3_eod_reconciliation
Create Date: 2026-04-26

说明：
- 新增 contextual_text TEXT 列，存储 LLM 抽取时同步生成的 50-100 字
  "局部上下文"（来源类型 / 时间 / 发言人 / 主话题）
- 用于在 embed 之前给 chunk 加定位信息，参考 Anthropic Contextual Retrieval
  (2024-09)，实测 retrieval 失败率 −67%
- nullable，旧数据该列为 NULL，search 时退化为 summary+full_quote 的旧路径，
  完全向后兼容
- 不需要 backfill：新数据走新路径，老数据走老路径，自然 cross-over
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0014_claim_contextual_text"
down_revision: Union[str, None] = "0013_g3_eod_reconciliation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "claim_cards",
        sa.Column("contextual_text", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("claim_cards", "contextual_text")
