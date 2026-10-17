"""Doctor Pass · 补 model_registry / key_groups 缺表 + 后续修正

Revision ID: 0012_drill_pass_fixes
Revises: 0011_g1_2_finalize
Create Date: 2026-04-25

背景
----
G1 / G1.1 / G1.2 三轮 schema drift 收尾后做"doctor pass"全栈实验时
（docker compose up + alembic upgrade head + SQLite→PG 迁移），发现：

1. **`model_registry` / `key_groups` 两张 ORM 声明的表 alembic 完全没建**
   - 这两张表 ORM 在 `app/schemas/model_registry.py`，但从未进过任何
     migration（既不在 0001 也不在 0008 missing_tables）
   - 本地 SQLite `dev.db` 里有数据是因为早期跑过 `Base.metadata.create_all()`
   - 干净 PG 容器里 alembic upgrade head 后这两张表不存在 → 启动后
     用到这两表的代码会 `relation does not exist`
   - 这是真实的"生产环境从零起步会踩、本地开发感受不到"的坑

行为
----
- 幂等创建 `key_groups`、`model_registry` 两张表（已存在则跳过）
- 含 `UniqueConstraint('group_name', 'api_key')` 与 `('group_name', 'model')`
- 其余配套（time_sensitive BOOLEAN DEFAULT 1 在 PG 失败、is_contradicted="0"
  在 PG 失败、env bool 解析）直接修复在 0010 / 0011 / 迁移脚本里

降级
----
反向 drop 两张表（生产慎用，确保业务不再依赖再降级）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers
revision: str = "0012_drill_pass_fixes"
down_revision: Union[str, None] = "0011_g1_2_finalize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    # ── 1. key_groups ───────────────────────────────────────────────
    if not _table_exists("key_groups"):
        op.create_table(
            "key_groups",
            sa.Column("id", sa.String(length=26), primary_key=True),
            sa.Column("group_name", sa.String(length=100), nullable=False),
            sa.Column("api_key", sa.String(length=200), nullable=False),
            sa.Column("base_url", sa.String(length=300), nullable=True),
            sa.Column("label", sa.String(length=100), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False,
                      server_default=sa.text("true")),
            sa.Column("last_tested_at", sa.String(length=50), nullable=True),
            sa.Column("last_test_ok", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.String(length=50), nullable=False),
            sa.Column("updated_at", sa.String(length=50), nullable=False),
            sa.UniqueConstraint("group_name", "api_key", name="uq_key_group"),
        )

    # ── 2. model_registry ───────────────────────────────────────────
    if not _table_exists("model_registry"):
        op.create_table(
            "model_registry",
            sa.Column("id", sa.String(length=26), primary_key=True),
            sa.Column("group_name", sa.String(length=100), nullable=False),
            sa.Column("model", sa.String(length=200), nullable=False),
            sa.Column("pricing_type", sa.String(length=20), nullable=False),
            sa.Column("input_price_per_1m", sa.Float(), nullable=True),
            sa.Column("output_price_per_1m", sa.Float(), nullable=True),
            sa.Column("cache_read_price_per_1m", sa.Float(), nullable=True),
            sa.Column("cache_write_price_per_1m", sa.Float(), nullable=True),
            sa.Column("price_per_call", sa.Float(), nullable=True),
            sa.Column("is_available", sa.Boolean(), nullable=False,
                      server_default=sa.text("true")),
            sa.Column("unavailable_reason", sa.Text(), nullable=True),
            sa.Column("last_checked_at", sa.String(length=50), nullable=True),
            sa.Column("consecutive_failures", sa.String(length=10),
                      server_default=sa.text("'0'")),
            sa.Column("created_at", sa.String(length=50), nullable=False),
            sa.Column("updated_at", sa.String(length=50), nullable=False),
            sa.UniqueConstraint("group_name", "model", name="uq_model_registry"),
        )


def downgrade() -> None:
    if _table_exists("model_registry"):
        op.drop_table("model_registry")
    if _table_exists("key_groups"):
        op.drop_table("key_groups")
