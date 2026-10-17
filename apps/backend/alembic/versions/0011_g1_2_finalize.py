"""G1.2 · NOT NULL 收尾 + 索引 ORM 化

Revision ID: 0011_g1_2_finalize
Revises: 0010_column_drift_sweep
Create Date: 2026-04-25

背景
----
0010 完成 ADD COLUMN 类漂移后，autogen 仍剩两类残留：
- B. ~50 处 NOT NULL ALTER：ORM 声明 NOT NULL 但 DB 没强制
- C. ~24 个 idx_* DROP：ORM 没声明但 DB 有的索引

本 migration 处理：
- **B 全清**：dev.db audit 显示 69 个候选列全部 0 NULL → 直接 batch_alter_table ALTER
- **C ORM 化**：13 个 ORM model 已加 `__table_args__` 声明这 24 个索引
  → 本 migration 只需保证现有 DB 中索引存在（幂等 create_index 即可，
  现有 SQLite/PG 已有则跳过）；fresh build 走 ORM 自然建索引

行为
----
- **NOT NULL ALTER（69 列）**：用 `op.batch_alter_table` 兼容 SQLite
  - 按表分组处理（每个 batch 重建一次表）
  - 全部带 server_default 兜底，理论上即使有 NULL 行也能 backfill 后 ALTER
- **索引**：原本就在 0001/0002 建过，现有 DB 都有；本 migration 不新建
  （ORM `__table_args__` 让 fresh build 自动建）

幂等性
------
- batch_alter_table 内 alter_column nullable=False：SQLite 上重建表，
  保留所有数据；PG 上是 ALTER TABLE ... ALTER COLUMN ... SET NOT NULL
- 多次 upgrade 安全（NOT NULL 已经是 NOT NULL 时再 ALTER 是 no-op）

风险
----
- batch_alter_table 在 SQLite 上重建表，大表（如 market_state_cards 1934 行）
  耗时几百毫秒到秒级；生产 PG 直接 ALTER 微秒级
- 跑前请备份（用 P2-C `infra/backup/backup.py`）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011_g1_2_finalize"
down_revision: Union[str, None] = "0010_column_drift_sweep"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── NOT NULL 待 ALTER 的列（按表分组）─────────────────────────────
# 数据来自：dev.db audit（_g1_notnull_audit.py），全部 0 NULL 行
#
# 字段：(列名, 类型, server_default) — server_default 用于异常 NULL backfill 兜底
_NOT_NULL_BATCHES: list[tuple[str, list[tuple[str, sa.types.TypeEngine, str | None]]]] = [
    ("briefing_cards", [
        ("asset_scope", sa.JSON(), "'[]'"),
        ("supporting_points", sa.JSON(), "'[]'"),
        ("contrary_points", sa.JSON(), "'[]'"),
        ("key_risks", sa.JSON(), "'[]'"),
        ("invalidation_conditions", sa.JSON(), "'[]'"),
        ("source_claim_ids", sa.JSON(), "'[]'"),
        ("source_event_ids", sa.JSON(), "'[]'"),
        ("committee_level", sa.String(20), "'single'"),
    ]),
    ("claim_cards", [
        ("asset_scope", sa.JSON(), "'[]'"),
        ("theme_scope", sa.JSON(), "'[]'"),
        ("geographic_scope", sa.JSON(), "'[]'"),
        ("conditions", sa.JSON(), "'[]'"),
        ("invalidation_conditions", sa.JSON(), "'[]'"),
        ("reasoning_basis", sa.JSON(), "'[]'"),
        ("confidence", sa.Float(), "'0.8'"),
        ("source_trust_weight", sa.Float(), "'0.5'"),
        # BOOLEAN：写 "0" 在 SQLite 能过但 PG 拒绝（type mismatch），
        # 与 0001 原始 server_default="false" 保持一致。
        ("is_contradicted", sa.Boolean(), "false"),
        ("version", sa.Integer(), "1"),
    ]),
    ("claim_evidence_links", [
        ("relevance_score", sa.Float(), "'1.0'"),
    ]),
    ("claim_raw_source_links", [
        ("link_type", sa.String(50), "'extracted_from'"),
    ]),
    ("event_cards", [
        ("asset_scope", sa.JSON(), "'[]'"),
        ("theme_scope", sa.JSON(), "'[]'"),
        ("geographic_scope", sa.JSON(), "'[]'"),
        ("importance", sa.Float(), "'0.5'"),
        ("is_surprise", sa.Boolean(), "false"),
        ("extra_data", sa.JSON(), "'{}'"),
    ]),
    ("evidence_spans", [
        ("confidence", sa.Float(), "'1.0'"),
        ("extraction_method", sa.String(50), "'llm'"),
        ("metadata", sa.JSON(), "'{}'"),
    ]),
    ("flow_signal_cards", [
        ("continuity_days", sa.Integer(), "1"),
        ("is_trend_change", sa.Boolean(), "false"),
        ("extra_data", sa.JSON(), "'{}'"),
    ]),
    ("job_runs", [
        ("triggered_by", sa.String(50), "'system'"),
        ("input_params", sa.JSON(), "'{}'"),
        ("result_summary", sa.JSON(), "'{}'"),
        ("retry_count", sa.Integer(), "0"),
    ]),
    ("lifecycle_logs", [
        ("evidence_snapshot", sa.JSON(), "'{}'"),
    ]),
    ("market_state_cards", [
        ("key_support_levels", sa.JSON(), "'[]'"),
        ("key_resistance_levels", sa.JSON(), "'[]'"),
        ("extra_data", sa.JSON(), "'{}'"),
    ]),
    ("outcome_records", [
        ("logged_by", sa.String(50), "'user'"),
    ]),
    ("setup_cards", [
        ("matched_conditions", sa.JSON(), "'[]'"),
        ("unmatched_conditions", sa.JSON(), "'[]'"),
        ("disqualifying_flags", sa.JSON(), "'[]'"),
        ("invalidation_conditions", sa.JSON(), "'[]'"),
        ("supporting_claim_ids", sa.JSON(), "'[]'"),
        ("supporting_event_ids", sa.JSON(), "'[]'"),
        ("market_state_snapshot", sa.JSON(), "'{}'"),
        ("flow_snapshot", sa.JSON(), "'{}'"),
    ]),
    ("source_attachments", [
        ("file_size_bytes", sa.Integer(), "0"),
    ]),
    ("speaker_profiles", [
        ("aliases", sa.JSON(), "'[]'"),
        ("domain_tags", sa.JSON(), "'[]'"),
        ("trust_weight", sa.Float(), "'0.5'"),
        ("total_claims", sa.Integer(), "0"),
        ("verified_claims", sa.Integer(), "0"),
    ]),
    ("strategy_cards", [
        ("applicable_assets", sa.JSON(), "'[]'"),
        ("applicable_horizons", sa.JSON(), "'[]'"),
        ("applicable_regimes", sa.JSON(), "'[]'"),
        ("entry_conditions", sa.JSON(), "'[]'"),
        ("disqualifying_conditions", sa.JSON(), "'[]'"),
        ("exit_rules", sa.JSON(), "'[]'"),
        ("risk_factors", sa.JSON(), "'[]'"),
        ("confidence", sa.Float(), "'0.7'"),
        ("version", sa.Integer(), "1"),
        ("distilled_by", sa.String(50), "'manual'"),
    ]),
    ("strategy_source_links", [
        ("link_type", sa.String(50), "'distilled_from'"),
    ]),
    ("watchlist_items", [
        ("priority", sa.Integer(), "0"),
        ("config", sa.JSON(), "'{}'"),
    ]),
    ("watchlists", [
        ("config", sa.JSON(), "'{}'"),
    ]),
]


def _backfill_nulls(table: str, columns: list[tuple[str, sa.types.TypeEngine, str | None]]) -> None:
    """异常防御：万一 dev.db 之后又写入 NULL 行，先 backfill 再 ALTER."""
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(table):
        return
    for col_name, _, default_sql in columns:
        if default_sql is None:
            continue
        op.execute(
            sa.text(f"UPDATE {table} SET {col_name} = {default_sql} WHERE {col_name} IS NULL")
        )


def upgrade() -> None:
    bind = op.get_bind()
    for table, columns in _NOT_NULL_BATCHES:
        if not sa.inspect(bind).has_table(table):
            continue
        # 防御性 backfill
        _backfill_nulls(table, columns)
        # batch ALTER 兼容 SQLite
        with op.batch_alter_table(table) as batch:
            for col_name, col_type, default_sql in columns:
                kwargs = {"existing_type": col_type, "nullable": False}
                if default_sql is not None:
                    kwargs["existing_server_default"] = sa.text(default_sql)
                batch.alter_column(col_name, **kwargs)

    # ── 反向 ALTER：strategy_cards.core_logic ──────────────────────
    # ORM 改为 nullable=True（仅 summary 必填即可，core_logic 可在迭代中补充）
    if sa.inspect(bind).has_table("strategy_cards"):
        with op.batch_alter_table("strategy_cards") as batch:
            batch.alter_column("core_logic", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()

    # 反向：恢复 core_logic 为 NOT NULL
    if sa.inspect(bind).has_table("strategy_cards"):
        with op.batch_alter_table("strategy_cards") as batch:
            batch.alter_column("core_logic", existing_type=sa.Text(), nullable=False)

    for table, columns in reversed(_NOT_NULL_BATCHES):
        if not sa.inspect(bind).has_table(table):
            continue
        with op.batch_alter_table(table) as batch:
            for col_name, col_type, default_sql in columns:
                kwargs = {"existing_type": col_type, "nullable": True}
                if default_sql is not None:
                    kwargs["existing_server_default"] = sa.text(default_sql)
                batch.alter_column(col_name, **kwargs)
