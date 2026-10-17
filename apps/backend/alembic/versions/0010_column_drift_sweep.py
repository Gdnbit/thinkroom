"""G1.1 · 列级漂移清理（外科式）

Revision ID: 0010_column_drift_sweep
Revises: 0009_pgvector_embedding
Create Date: 2026-04-25

背景
----
0008 / 0009 把 32 张表 + embedding 列纳入 Alembic 后，autogen 仍检测到 ~95 处差异。
分析后发现这些差异分两类：

A. **ORM 新增字段历史遗留**（~35 处）
   - `claim_cards.time_sensitive` / `expiry_hint`
   - `event_cards.*`（V2 truth_status / outcome / source_tier / sentiment ... 15+）
   - `raw_sources.content_hash` / `user_trust_level` / `collection_reason` / `user_note`
   - `speaker_profiles.*`（V2 多维画像 14+）
   现在通过 `app/main.py::_migrate_missing_columns()` 启动时兜底；本 migration 把它纳入 Alembic

B. **NOT NULL 约束 + DROP INDEX**（~60 处）
   - 几十个 NOT NULL alter（ORM 声明但 DB 没强制）
   - 多个 idx_* 在 ORM 已不存在但 DB 还在
   这些是历史索引/约束差异，**不在本 migration 处理**：
   - NOT NULL ALTER：在已有数据库上需 backfill 审计，单独工作；目前列都有 ORM `default=`
     ，新行 OK，旧 NULL 不影响业务
   - DROP INDEX：legacy 索引很多是 FK 性能索引（如 `idx_claim_cards_speaker_id`），
     ORM 应该 ADD `index=True` 而不是 DB drop；交给后续 schema 维护

行为
----
- **Postgres + SQLite 跨方言**：用 batch_alter_table 兼容 SQLite 不支持 ALTER COLUMN
- **server_default 兜底**：所有 NOT NULL 新列都带 server_default，避免 backfill 失败
- **完全幂等**：列已存在时跳过

本 migration 完成后：
- fresh build：`alembic upgrade head` 出 32 表 + 完整列 + ix_raw_sources_content_hash 索引
- 现有 dev.db / 生产 PG：`alembic upgrade head` 仅补缺列，无副作用
- `_migrate_missing_columns()` 在干净环境是 no-op；保留作"未来漂移检测告警"的安全网
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_column_drift_sweep"
down_revision: Union[str, None] = "0009_pgvector_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── 工具函数 ──────────────────────────────────────────────────────


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    return column in cols


def _has_index(table: str, name: str) -> bool:
    bind = op.get_bind()
    try:
        return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes(table))
    except Exception:
        return False


def _add_column_if_missing(table: str, col: sa.Column) -> None:
    """幂等添加列；若 NOT NULL 必须带 server_default."""
    if _has_column(table, col.name):
        return
    op.add_column(table, col)


# ── upgrade ───────────────────────────────────────────────────────


# 列定义集中放：(table, [Column(...)])
# server_default 用 SQL 字面量，确保 Postgres + SQLite 两端语义一致
_NEW_COLUMNS: list[tuple[str, list[sa.Column]]] = [
    (
        "claim_cards",
        [
            # 注意：BOOLEAN server_default 必须用 'true'/'false' 字面量
            # 写 sa.text("1") 在 SQLite 能过（int→bool 隐式转换），
            # 但 Postgres 严格类型检查会拒绝："column is of type boolean
            # but default expression is of type integer"。
            # SQLite 3.23+ 也接受 TRUE/FALSE 关键字，跨方言安全。
            sa.Column("time_sensitive", sa.Boolean(), nullable=False,
                      server_default=sa.text("true")),
            sa.Column("expiry_hint", sa.String(length=200), nullable=True),
        ],
    ),
    (
        "raw_sources",
        [
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("user_trust_level", sa.String(length=20), nullable=True),
            sa.Column("collection_reason", sa.String(length=50), nullable=True),
            sa.Column("user_note", sa.Text(), nullable=True),
        ],
    ),
    (
        "event_cards",
        [
            sa.Column("truth_status", sa.String(length=30), nullable=False,
                      server_default=sa.text("'pending'")),
            sa.Column("truth_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("truth_source", sa.String(length=500), nullable=True),
            sa.Column("outcome_status", sa.String(length=30), nullable=False,
                      server_default=sa.text("'pending'")),
            sa.Column("outcome_observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome_notes", sa.Text(), nullable=True),
            sa.Column("source_tier", sa.String(length=30), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column("sentiment", sa.Float(), nullable=True),
            sa.Column("novelty", sa.Float(), nullable=True),
            sa.Column("trust_weight", sa.Float(), nullable=False,
                      server_default=sa.text("'0.5'")),
            sa.Column("claim_text", sa.Text(), nullable=True),
            sa.Column("invalidation_window", sa.DateTime(timezone=True), nullable=True),
            sa.Column("followup_event_ids", sa.JSON(), nullable=False,
                      server_default=sa.text("'[]'")),
            sa.Column("user_override_truth", sa.String(length=30), nullable=True),
            sa.Column("user_override_outcome", sa.String(length=30), nullable=True),
            sa.Column("user_override_notes", sa.Text(), nullable=True),
        ],
    ),
    (
        "speaker_profiles",
        [
            sa.Column("perspective_value", sa.Float(), nullable=False,
                      server_default=sa.text("'0.5'")),
            sa.Column("emotional_intensity", sa.Float(), nullable=False,
                      server_default=sa.text("'0.3'")),
            sa.Column("logic_robustness", sa.Float(), nullable=False,
                      server_default=sa.text("'0.5'")),
            sa.Column("applicable_domains", sa.JSON(), nullable=False,
                      server_default=sa.text("'[]'")),
            sa.Column("time_half_life", sa.String(length=20), nullable=False,
                      server_default=sa.text("'short'")),
            sa.Column("contrarian_value", sa.Float(), nullable=False,
                      server_default=sa.text("'0.0'")),
            sa.Column("hit_count", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("miss_count", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("partial_hit_count", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("avg_deviation_pct", sa.Float(), nullable=True),
            sa.Column("best_asset_classes", sa.JSON(), nullable=False,
                      server_default=sa.text("'[]'")),
            sa.Column("worst_asset_classes", sa.JSON(), nullable=False,
                      server_default=sa.text("'[]'")),
            sa.Column("usage_recommendation", sa.Text(), nullable=True),
            sa.Column("last_calibrated_at", sa.DateTime(timezone=True), nullable=True),
        ],
    ),
]


# 索引定义：(name, table, columns, unique)
_NEW_INDEXES: list[tuple[str, str, list[str], bool]] = [
    ("ix_raw_sources_content_hash", "raw_sources", ["content_hash"], True),
]


def upgrade() -> None:
    # ── 1. 补齐缺失列 ─────────────────────────
    for table_name, columns in _NEW_COLUMNS:
        bind = op.get_bind()
        # 表不存在时跳过（理论上 0008 已建全）
        if not sa.inspect(bind).has_table(table_name):
            continue
        for col in columns:
            _add_column_if_missing(table_name, col)

    # ── 2. 新增唯一索引 ───────────────────────
    for idx_name, table_name, cols, unique in _NEW_INDEXES:
        bind = op.get_bind()
        if not sa.inspect(bind).has_table(table_name):
            continue
        if _has_index(table_name, idx_name):
            continue
        op.create_index(idx_name, table_name, cols, unique=unique)


def downgrade() -> None:
    # ── 1. 删索引 ─────────────────────────────
    for idx_name, table_name, _cols, _unique in reversed(_NEW_INDEXES):
        if _has_index(table_name, idx_name):
            op.drop_index(idx_name, table_name=table_name)

    # ── 2. 删列（按反向顺序） ─────────────────
    for table_name, columns in reversed(_NEW_COLUMNS):
        for col in reversed(columns):
            if _has_column(table_name, col.name):
                op.drop_column(table_name, col.name)
