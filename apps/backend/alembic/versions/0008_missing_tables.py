"""G1 补齐 12 张 ORM 声明但 Alembic 缺失的表 —— 2026-04-24 新增

Revision ID: 0008_missing_tables
Revises: 0007_debate_model_by_seat
Create Date: 2026-04-24

背景
----
截止到 0007，Alembic 管理 20 张表；而 ORM 实际声明了 32 张，差 12 张：
  chat_sessions / chat_messages / debate_records / debate_rounds /
  final_adjudications / decision_snapshots / fund_profiles /
  fund_estimate_snapshots / market_snapshots / processing_traces /
  source_health_logs / user_overrides

这些表在现有生产库里靠 `app.main._migrate_missing_columns()` + `Base.metadata.create_all()`
兜底建出来，但 fresh Postgres clone 必须先跑一次后端才会有这些表，不符合 Alembic 应是
"schema 真理源"的约定。

本 migration 把这 12 张表纳入 Alembic 管理：
- **Fresh SQLite/Postgres** → 直接建表
- **现有生产库**（表已存在） → 逐表 `inspector.get_table_names()` 检查并跳过，幂等无损

JSON 字段统一用 `_JSONB()` helper（Postgres=JSONB 二进制+索引友好，SQLite=JSON/TEXT）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0008_missing_tables"
down_revision: Union[str, None] = "0007_debate_model_by_seat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _JSONB():
    """跨方言 JSONB: Postgres=JSONB（二进制+索引）, SQLite=JSON（TEXT）."""
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _table_exists(name: str) -> bool:
    """检查当前 DB 是否已有该表（用于幂等建表）."""
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def _create_if_missing(name: str, *cols, **kwargs):
    """只在表不存在时建表；存在则跳过（生产库兼容）."""
    if _table_exists(name):
        return
    op.create_table(name, *cols, **kwargs)


def _index_if_table_created(name: str, table: str, *args, **kwargs):
    """只在表是本次新建的（假设表若存在，索引已有）时建索引；避免生产 DB 上重复建索引报错."""
    # 简化：若建表被跳过，索引也跳过（假定生产库已有相应索引或自行管理）
    bind = op.get_bind()
    existing = {ix["name"] for ix in sa.inspect(bind).get_indexes(table)} if _table_exists(table) else set()
    if name in existing:
        return
    op.create_index(name, table, *args, **kwargs)


# ── 12 张缺失表的建表定义 ────────────────────────────────────────
# 顺序：无依赖的在前，有 FK 的在后


def upgrade() -> None:
    # 1. chat_sessions（无外键）
    _create_if_missing(
        "chat_sessions",
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. chat_messages（FK → chat_sessions）
    _create_if_missing(
        "chat_messages",
        sa.Column("session_id", sa.String(26), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_chat_messages_session_id", "chat_messages", ["session_id"], unique=False)

    # 3. decision_snapshots（无外键，signal_scorecard / activated_seats 等重用 JSONB）
    _create_if_missing(
        "decision_snapshots",
        sa.Column("trigger_type", sa.String(30), nullable=False),
        sa.Column("trigger_id", sa.String(26), nullable=False),
        sa.Column("asset", sa.String(50), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claims_snapshot", _JSONB(), nullable=False),
        sa.Column("claims_count", sa.Integer(), nullable=False),
        sa.Column("data_coverage", _JSONB(), nullable=False),
        sa.Column("market_snapshot", _JSONB(), nullable=False),
        sa.Column("flow_snapshot", _JSONB(), nullable=False),
        sa.Column("events_snapshot", _JSONB(), nullable=False),
        sa.Column("macro_snapshot", _JSONB(), nullable=False),
        sa.Column("signal_scorecard", _JSONB(), nullable=False),
        sa.Column("activated_seats", _JSONB(), nullable=False),
        sa.Column("evidence_budget", _JSONB(), nullable=False),
        sa.Column("final_direction", sa.String(20), nullable=True),
        sa.Column("final_conviction", sa.Float(), nullable=True),
        sa.Column("final_action", sa.String(50), nullable=True),
        sa.Column("synthesis_summary", sa.Text(), nullable=True),
        sa.Column("outcome_direction", sa.String(20), nullable=True),
        sa.Column("outcome_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_pnl_pct", sa.Float(), nullable=True),
        sa.Column("replay_notes", sa.Text(), nullable=True),
        sa.Column("replay_status", sa.String(20), nullable=False),
        sa.Column("misleading_evidence", _JSONB(), nullable=False),
        sa.Column("key_missed_signals", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_decision_snapshots_asset", "decision_snapshots", ["asset"], unique=False)
    _index_if_table_created("ix_decision_snapshots_trigger_id", "decision_snapshots", ["trigger_id"], unique=False)

    # 4. fund_profiles
    _create_if_missing(
        "fund_profiles",
        sa.Column("fund_code", sa.String(20), nullable=False),
        sa.Column("fund_name", sa.String(200), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("last_nav", sa.Float(), nullable=True),
        sa.Column("last_nav_date", sa.Date(), nullable=True),
        sa.Column("basket", _JSONB(), nullable=False),
        sa.Column("beta", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_fund_profiles_fund_code", "fund_profiles", ["fund_code"], unique=True)

    # 5. fund_estimate_snapshots
    _create_if_missing(
        "fund_estimate_snapshots",
        sa.Column("fund_code", sa.String(20), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("est_nav", sa.Float(), nullable=True),
        sa.Column("est_pct_change_1d", sa.Float(), nullable=True),
        sa.Column("confidence_level", sa.String(20), nullable=False),
        sa.Column("ref_index", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(30), nullable=False),
        sa.Column("meta", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_fund_estimate_snapshots_fund_code", "fund_estimate_snapshots", ["fund_code"], unique=False)
    _index_if_table_created("ix_fund_estimate_snapshots_ts", "fund_estimate_snapshots", ["ts"], unique=False)

    # 6. market_snapshots
    _create_if_missing(
        "market_snapshots",
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("pct_change_1d", sa.Float(), nullable=True),
        sa.Column("source_name", sa.String(100), nullable=True),
        sa.Column("source_symbol", sa.String(100), nullable=True),
        sa.Column("is_fallback", sa.Boolean(), nullable=False),
        sa.Column("quality_flag", sa.String(20), nullable=False),
        sa.Column("meta", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_market_snapshots_market", "market_snapshots", ["market"], unique=False)
    _index_if_table_created("ix_market_snapshots_symbol", "market_snapshots", ["symbol"], unique=False)
    _index_if_table_created("ix_market_snapshots_ts", "market_snapshots", ["ts"], unique=False)

    # 7. processing_traces
    _create_if_missing(
        "processing_traces",
        sa.Column("source_id", sa.String(26), nullable=False),
        sa.Column("job_id", sa.String(26), nullable=True),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_snapshot", _JSONB(), nullable=False),
        sa.Column("output_snapshot", _JSONB(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("meta", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_processing_traces_job_id", "processing_traces", ["job_id"], unique=False)
    _index_if_table_created("ix_processing_traces_source_id", "processing_traces", ["source_id"], unique=False)

    # 8. source_health_logs
    _create_if_missing(
        "source_health_logs",
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("meta", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_source_health_logs_checked_at", "source_health_logs", ["checked_at"], unique=False)
    _index_if_table_created("ix_source_health_logs_source_name", "source_health_logs", ["source_name"], unique=False)
    _index_if_table_created("ix_source_health_logs_symbol", "source_health_logs", ["symbol"], unique=False)

    # 9. user_overrides
    _create_if_missing(
        "user_overrides",
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.String(26), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("override_type", sa.String(30), nullable=False),
        sa.Column("extra_data", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _index_if_table_created("ix_user_overrides_target_id", "user_overrides", ["target_id"], unique=False)
    _index_if_table_created("ix_user_overrides_target_type", "user_overrides", ["target_type"], unique=False)

    # 10. debate_records（FK → briefing_cards）
    _create_if_missing(
        "debate_records",
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("asset", sa.String(50), nullable=False),
        sa.Column("committee_level", sa.String(20), nullable=False),
        sa.Column("model_by_seat", _JSONB(), nullable=True),
        sa.Column("model_bull", sa.String(100), nullable=True),
        sa.Column("model_bear", sa.String(100), nullable=True),
        sa.Column("model_neutral", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("context_claims", _JSONB(), nullable=False),
        sa.Column("context_events", _JSONB(), nullable=False),
        sa.Column("context_market", _JSONB(), nullable=False),
        sa.Column("final_direction", sa.String(20), nullable=True),
        sa.Column("final_conviction", sa.Float(), nullable=True),
        sa.Column("final_action", sa.String(50), nullable=True),
        sa.Column("consensus_level", sa.String(20), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("briefing_id", sa.String(26), nullable=True),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["briefing_id"], ["briefing_cards.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 11. debate_rounds（FK → debate_records CASCADE）
    _create_if_missing(
        "debate_rounds",
        sa.Column("debate_id", sa.String(26), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("key_arguments", _JSONB(), nullable=False),
        sa.Column("challenges", _JSONB(), nullable=False),
        sa.Column("concessions", _JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("direction", sa.String(20), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["debate_id"], ["debate_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 12. final_adjudications（FK → debate_records CASCADE, UNIQUE debate_id）
    _create_if_missing(
        "final_adjudications",
        sa.Column("debate_id", sa.String(26), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("conviction", sa.Float(), nullable=False),
        sa.Column("recommended_action", sa.String(50), nullable=False),
        sa.Column("consensus_level", sa.String(20), nullable=False),
        sa.Column("synthesis", sa.Text(), nullable=False),
        sa.Column("bull_score", sa.Float(), nullable=True),
        sa.Column("bear_score", sa.Float(), nullable=True),
        sa.Column("key_agreements", _JSONB(), nullable=False),
        sa.Column("key_disagreements", _JSONB(), nullable=False),
        sa.Column("minority_view", sa.Text(), nullable=True),
        sa.Column("invalidation_conditions", _JSONB(), nullable=False),
        sa.Column("adjudicated_by", sa.String(100), nullable=True),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("structure_score", sa.Float(), nullable=True),
        sa.Column("structure_warnings", _JSONB(), nullable=False),
        sa.Column("risk_warnings", _JSONB(), nullable=False),
        sa.Column("hedging_advice", sa.Text(), nullable=True),
        sa.Column("action_economics", _JSONB(), nullable=False),
        sa.Column("id", sa.String(26), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["debate_id"], ["debate_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("debate_id"),
    )


def _drop_if_exists(name: str) -> None:
    if _table_exists(name):
        op.drop_table(name)


def downgrade() -> None:
    """按 FK 依赖倒序 drop（有外键引用的先 drop 引用方）."""
    # 反向：先 drop 引用 debate_records 的表
    _drop_if_exists("final_adjudications")
    _drop_if_exists("debate_rounds")
    _drop_if_exists("debate_records")
    # 再 drop 引用 chat_sessions 的
    _drop_if_exists("chat_messages")
    _drop_if_exists("chat_sessions")
    # 无依赖的
    _drop_if_exists("user_overrides")
    _drop_if_exists("source_health_logs")
    _drop_if_exists("processing_traces")
    _drop_if_exists("market_snapshots")
    _drop_if_exists("fund_estimate_snapshots")
    _drop_if_exists("fund_profiles")
    _drop_if_exists("decision_snapshots")
