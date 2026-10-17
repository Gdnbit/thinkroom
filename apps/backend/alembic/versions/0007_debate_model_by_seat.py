"""A6: debate_records.model_by_seat JSON column (replaces model_bull/bear/neutral)

Revision ID: 0007_debate_model_by_seat
Revises: 0006_embedding_dim_1024
Create Date: 2026-04-17

说明：
- 新增 model_by_seat JSON 列，存 {"baseline": "x", "probability": "y", "risk": "z", ...}
- 旧三列 model_bull/model_bear/model_neutral 保留以保持向后兼容（dual-read）
- 历史数据由应用层读时 lazily fallback 到旧列（见 committee.py）
- 下一次大版本（0010+）再移除旧列
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0007_debate_model_by_seat"
down_revision: Union[str, None] = "0006_embedding_dim_1024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 新增 model_by_seat JSON 列；历史行保持 NULL，读时 fallback 到旧三列
    # 幂等保护：仅当 debate_records 表已存在时才 ALTER
    # （见 GAP_LIST「alembic-orm drift」—— 9 张表目前仅由 create_all 建，
    #  在 fresh SQLite 上 alembic upgrade 时本表不存在，跳过即可）
    from sqlalchemy import inspect as _inspect
    bind = op.get_bind()
    if "debate_records" not in _inspect(bind).get_table_names():
        return
    existing_cols = {c["name"] for c in _inspect(bind).get_columns("debate_records")}
    if "model_by_seat" in existing_cols:
        return
    with op.batch_alter_table("debate_records") as batch:
        batch.add_column(sa.Column("model_by_seat", sa.JSON(), nullable=True))


def downgrade() -> None:
    from sqlalchemy import inspect as _inspect
    bind = op.get_bind()
    if "debate_records" not in _inspect(bind).get_table_names():
        return
    with op.batch_alter_table("debate_records") as batch:
        batch.drop_column("model_by_seat")
