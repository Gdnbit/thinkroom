"""P2-A · claim_cards.embedding 列正式纳入 Alembic 管理（跨方言）

Revision ID: 0009_pgvector_embedding
Revises: 0008_missing_tables
Create Date: 2026-04-24

背景
----
历史上 0004 / 0006 migration 是空操作（带注释："标准 postgres:16 不含 pgvector 扩展，跳过"），
claim_cards.embedding 列一直靠 ORM `create_all` + 条件编译（`claim_card.py` 的
`_USE_VECTOR_COL = _HAS_PGVECTOR and settings.PGVECTOR_ENABLED`）兜底：
- Postgres + pgvector + PGVECTOR_ENABLED=true → ORM 给 Vector(EMBEDDING_DIM) 列
- 其他场景（SQLite / Postgres 无 pgvector） → ORM 给 Text 列

这个设计工作是工作，但 Alembic 不管 embedding，fresh 环境搭建必须跑一次后端才有列。
本 migration 终结这个隐式依赖：把 embedding 列正式纳入 Alembic。

行为
----
- **Postgres + pgvector 扩展可用** → `ADD COLUMN embedding vector(EMBEDDING_DIM)` + HNSW 索引
- **Postgres 无 pgvector 扩展** → `ADD COLUMN embedding TEXT`（JSON 序列化 fallback）
- **SQLite** → `ADD COLUMN embedding TEXT`（Tier ② sqlite-vec 单独管理 vec0 表）

所有操作都幂等：列已存在则跳过；索引已存在则跳过。

注意
----
- `EMBEDDING_DIM` 从 `app.config.settings` 读取，默认 2560（qwen3-embedding-4b）
- 改 EMBEDDING_DIM 的场景（切模型）需要额外 migration 先 drop 列再重建，不是本 migration 范围
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import settings


revision: str = "0009_pgvector_embedding"
down_revision: Union[str, None] = "0008_missing_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = sa.inspect(bind).get_columns(table)
    return any(c["name"] == column for c in cols)


def _has_index(table: str, name: str) -> bool:
    bind = op.get_bind()
    try:
        return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes(table))
    except Exception:
        return False


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _pgvector_available() -> bool:
    """检查 Postgres 是否已安装 vector 扩展."""
    if not _is_postgres():
        return False
    bind = op.get_bind()
    try:
        rows = bind.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).fetchall()
        return bool(rows)
    except Exception:
        return False


def upgrade() -> None:
    if _has_column("claim_cards", "embedding"):
        # 已有列（ORM create_all 或 0004/0006 手动 ALTER 建过），跳过建列，只补索引
        _ensure_hnsw_index_if_postgres()
        return

    if _pgvector_available():
        # Postgres + pgvector：用原生 vector(N) 类型
        dim = settings.EMBEDDING_DIM
        op.execute(f"ALTER TABLE claim_cards ADD COLUMN IF NOT EXISTS embedding vector({dim})")
        _ensure_hnsw_index_if_postgres()
    elif _is_postgres():
        # Postgres 但无 pgvector 扩展 → 回退 TEXT
        op.add_column("claim_cards", sa.Column("embedding", sa.Text(), nullable=True))
    else:
        # SQLite → TEXT（JSON 序列化由上游 index_claim 处理）
        op.add_column("claim_cards", sa.Column("embedding", sa.Text(), nullable=True))


def _ensure_hnsw_index_if_postgres():
    """仅 Postgres + pgvector 建 HNSW 向量索引；cosine_ops 用于 <=> 距离操作符.

    pgvector 限制：HNSW / IVFFlat 索引列维度不得超过 2000。
    若 EMBEDDING_DIM > 2000（如 qwen3-embedding-4b 的 2560），跳过建索引，
    依赖 pgvector 顺序扫描 + `<=>` 距离算子（性能足以应付百万级以内的 claim）。
    """
    if not _pgvector_available():
        return
    if _has_index("claim_cards", "ix_claim_cards_embedding_hnsw"):
        return
    dim = settings.EMBEDDING_DIM
    if dim > 2000:
        # pgvector HNSW 上限 2000 维；超限记个 notice，走顺序扫描 + <=> 算子
        op.execute(
            f"DO $$ BEGIN "
            f"RAISE NOTICE 'claim_cards.embedding dim={dim} > 2000; "
            f"skipping HNSW index (pgvector limit). "
            f"Using sequential scan + cosine distance operator.'; "
            f"END $$;"
        )
        return
    # HNSW 比 IVFFlat 检索质量更好，写入稍慢；m=16 ef_construction=64 是常用默认
    # 用 vector_cosine_ops 因为 claim 相似度用 cosine 距离；如切 L2 改 vector_l2_ops
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_claim_cards_embedding_hnsw "
        "ON claim_cards USING hnsw (embedding vector_cosine_ops) "
        "WITH (m=16, ef_construction=64)"
    )


def downgrade() -> None:
    """移除 embedding 列及索引."""
    if _is_postgres() and _has_index("claim_cards", "ix_claim_cards_embedding_hnsw"):
        op.execute("DROP INDEX IF EXISTS ix_claim_cards_embedding_hnsw")
    if _has_column("claim_cards", "embedding"):
        op.drop_column("claim_cards", "embedding")
