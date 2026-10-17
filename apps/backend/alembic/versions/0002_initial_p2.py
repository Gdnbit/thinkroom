"""P0 初始数据库 Schema — Part 2

Revision ID: 0002_initial_p2
Revises: 0001_initial
Create Date: 2026-03-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_initial_p2"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _JSONB():
    """Cross-dialect JSONB: Postgres=JSONB, SQLite=JSON."""
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # market_state_cards
    op.create_table(
        "market_state_cards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("asset", sa.String(50), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Float),
        sa.Column("price_change_1d", sa.Float),
        sa.Column("price_change_7d", sa.Float),
        sa.Column("price_change_30d", sa.Float),
        sa.Column("trend_direction", sa.String(20)),
        sa.Column("trend_strength", sa.Float),
        sa.Column("key_support_levels", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("key_resistance_levels", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("rsi_14", sa.Float),
        sa.Column("ma_20", sa.Float),
        sa.Column("ma_50", sa.Float),
        sa.Column("ma_200", sa.Float),
        sa.Column("volume_vs_avg", sa.Float),
        sa.Column("atr_14", sa.Float),
        sa.Column("structure_notes", sa.Text),
        sa.Column("volatility_state", sa.String(20)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("data_source", sa.String(100)),
        sa.Column("extra_data", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_market_state_asset_time", "market_state_cards", ["asset", "timeframe", "snapshot_at"])

    # flow_signal_cards
    op.create_table(
        "flow_signal_cards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("asset", sa.String(50), nullable=False),
        sa.Column("signal_type", sa.String(50), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(20)),
        sa.Column("magnitude", sa.Float),
        sa.Column("raw_value", sa.Float),
        sa.Column("normalized_value", sa.Float),
        sa.Column("description", sa.Text),
        sa.Column("continuity_days", sa.Integer, server_default="1"),
        sa.Column("is_trend_change", sa.Boolean, server_default="false"),
        sa.Column("previous_signal_id", sa.String(26)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("data_source", sa.String(100)),
        sa.Column("extra_data", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_flow_signal_asset", "flow_signal_cards", ["asset", "signal_type", "observed_at"])

    # strategy_cards
    op.create_table(
        "strategy_cards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("strategy_name", sa.String(300), nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("core_logic", sa.Text, nullable=False),
        sa.Column("applicable_assets", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("applicable_horizons", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("applicable_regimes", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("entry_conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("disqualifying_conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("exit_rules", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("risk_factors", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("position_sizing_notes", sa.Text),
        sa.Column("historical_win_rate", sa.Float),
        sa.Column("confidence", sa.Float, server_default="0.7"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("distilled_by", sa.String(50), server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # strategy_source_links
    op.create_table(
        "strategy_source_links",
        sa.Column("strategy_id", sa.String(26), sa.ForeignKey("strategy_cards.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.String(26), sa.ForeignKey("raw_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("link_type", sa.String(50), server_default="distilled_from"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # setup_cards
    op.create_table(
        "setup_cards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("strategy_id", sa.String(26), sa.ForeignKey("strategy_cards.id", ondelete="SET NULL")),
        sa.Column("asset", sa.String(50), nullable=False),
        sa.Column("timeframe", sa.String(20)),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("strategy_fit_score", sa.Float),
        sa.Column("evidence_strength_score", sa.Float),
        sa.Column("overall_score", sa.Float),
        sa.Column("matched_conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("unmatched_conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("disqualifying_flags", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("recommended_action", sa.String(50)),
        sa.Column("action_notes", sa.Text),
        sa.Column("invalidation_conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("supporting_claim_ids", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("supporting_event_ids", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("market_state_snapshot", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("flow_snapshot", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_setup_cards_status", "setup_cards", ["status"])
    op.create_index("idx_setup_cards_asset", "setup_cards", ["asset"])

    # briefing_cards
    op.create_table(
        "briefing_cards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("question", sa.Text),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("setup_quality", sa.String(20), nullable=False),
        sa.Column("time_horizon", sa.String(20)),
        sa.Column("asset_scope", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("executive_summary", sa.Text, nullable=False),
        sa.Column("supporting_points", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("contrary_points", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("key_risks", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("strategy_fit_summary", sa.Text),
        sa.Column("recommended_action", sa.String(50), nullable=False),
        sa.Column("invalidation_conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("setup_id", sa.String(26), sa.ForeignKey("setup_cards.id", ondelete="SET NULL")),
        sa.Column("source_claim_ids", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("source_event_ids", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("model_used", sa.String(100)),
        sa.Column("committee_level", sa.String(20), server_default="single"),
        sa.Column("generation_time_ms", sa.Integer),
        sa.Column("token_count", sa.Integer),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_briefing_cards_created_at", "briefing_cards", ["created_at"])
    op.create_index("idx_briefing_cards_asset_scope", "briefing_cards", ["asset_scope"], postgresql_using="gin")

    # outcome_records
    op.create_table(
        "outcome_records",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("briefing_id", sa.String(26), sa.ForeignKey("briefing_cards.id", ondelete="SET NULL")),
        sa.Column("claim_id", sa.String(26), sa.ForeignKey("claim_cards.id", ondelete="SET NULL")),
        sa.Column("setup_id", sa.String(26), sa.ForeignKey("setup_cards.id", ondelete="SET NULL")),
        sa.Column("outcome_type", sa.String(50), nullable=False),
        sa.Column("is_correct", sa.Boolean),
        sa.Column("actual_direction", sa.String(20)),
        sa.Column("predicted_direction", sa.String(20)),
        sa.Column("entry_price", sa.Float),
        sa.Column("exit_price", sa.Float),
        sa.Column("pnl_pct", sa.Float),
        sa.Column("max_drawdown_pct", sa.Float),
        sa.Column("prediction_made_at", sa.DateTime(timezone=True)),
        sa.Column("outcome_observed_at", sa.DateTime(timezone=True)),
        sa.Column("horizon_days", sa.Integer),
        sa.Column("review_notes", sa.Text),
        sa.Column("what_went_right", sa.Text),
        sa.Column("what_went_wrong", sa.Text),
        sa.Column("lesson_learned", sa.Text),
        sa.Column("logged_by", sa.String(50), server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # watchlists
    op.create_table(
        "watchlists",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("list_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("config", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # watchlist_items
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("watchlist_id", sa.String(26), sa.ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_type", sa.String(30), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("priority", sa.Integer, server_default="0"),
        sa.Column("notes", sa.Text),
        sa.Column("config", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_watchlist_items_watchlist_id", "watchlist_items", ["watchlist_id"])

    # job_runs
    op.create_table(
        "job_runs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("job_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("triggered_by", sa.String(50), server_default="system"),
        sa.Column("input_params", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("result_summary", _JSONB(), server_default=sa.text("'{}'") ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("error_message", sa.Text),
        sa.Column("retry_count", sa.Integer, server_default="0"),
        sa.Column("related_object_id", sa.String(26)),
        sa.Column("related_object_type", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_job_runs_status", "job_runs", ["status"])
    op.create_index("idx_job_runs_job_type", "job_runs", ["job_type"])


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
    op.drop_table("outcome_records")
    op.drop_table("briefing_cards")
    op.drop_table("setup_cards")
    op.drop_table("strategy_source_links")
    op.drop_table("strategy_cards")
    op.drop_table("flow_signal_cards")
    op.drop_table("market_state_cards")
