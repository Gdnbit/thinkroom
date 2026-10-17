"""P0 初始数据库 Schema — Part 1

Revision ID: 0001_initial
Revises:
Create Date: 2026-03-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _JSONB():
    """Cross-dialect JSONB: Postgres=JSONB（二进制+索引）, SQLite=JSON（TEXT）."""
    return postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # raw_sources
    op.create_table(
        "raw_sources",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("original_url", sa.String(2000)),
        sa.Column("publisher", sa.String(200)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("raw_text", sa.Text),
        sa.Column("language", sa.String(10), nullable=False, server_default="zh"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("processing_error", sa.Text),
        sa.Column("speaker_hint", sa.String(200)),
        sa.Column("theme_hint", sa.String(200)),
        sa.Column("ingested_at", sa.DateTime(timezone=True)),
        sa.Column("word_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_raw_sources_status", "raw_sources", ["status"])
    op.create_index("idx_raw_sources_created_at", "raw_sources", ["created_at"])

    # source_attachments
    op.create_table(
        "source_attachments",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("source_id", sa.String(26), sa.ForeignKey("raw_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attachment_type", sa.String(50), nullable=False),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("file_size_bytes", sa.Integer, server_default="0"),
        sa.Column("ocr_text", sa.Text),
        sa.Column("transcription_text", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_source_attachments_source_id", "source_attachments", ["source_id"])

    # speaker_profiles
    op.create_table(
        "speaker_profiles",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aliases", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("affiliation", sa.String(200)),
        sa.Column("role", sa.String(200)),
        sa.Column("domain_tags", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("trust_weight", sa.Float, server_default="0.5"),
        sa.Column("track_record_summary", sa.Text),
        sa.Column("style_notes", sa.Text),
        sa.Column("hit_rate", sa.Float),
        sa.Column("total_claims", sa.Integer, server_default="0"),
        sa.Column("verified_claims", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_speaker_profiles_name", "speaker_profiles", ["name"])

    # evidence_spans
    op.create_table(
        "evidence_spans",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("source_id", sa.String(26), sa.ForeignKey("raw_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("span_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("page_or_timestamp", sa.String(100)),
        sa.Column("confidence", sa.Float, server_default="1.0"),
        sa.Column("extraction_method", sa.String(50), server_default="llm"),
        sa.Column("char_start", sa.Integer),
        sa.Column("char_end", sa.Integer),
        sa.Column("metadata", _JSONB(), server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_evidence_spans_source_id", "evidence_spans", ["source_id"])

    # claim_cards
    op.create_table(
        "claim_cards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("full_quote", sa.Text),
        sa.Column("claim_type", sa.String(50), nullable=False),
        sa.Column("stance", sa.String(20), nullable=False),
        sa.Column("time_horizon", sa.String(20)),
        sa.Column("asset_scope", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("theme_scope", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("geographic_scope", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("invalidation_conditions", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("reasoning_basis", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("confidence", sa.Float, server_default="0.8"),
        sa.Column("source_trust_weight", sa.Float, server_default="0.5"),
        sa.Column("is_contradicted", sa.Boolean, server_default="false"),
        sa.Column("contradiction_notes", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("last_lifecycle_check", sa.DateTime(timezone=True)),
        sa.Column("expiry_reason", sa.Text),
        sa.Column("speaker_id", sa.String(26), sa.ForeignKey("speaker_profiles.id", ondelete="SET NULL")),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("parent_claim_id", sa.String(26), sa.ForeignKey("claim_cards.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_claim_cards_status", "claim_cards", ["status"])
    op.create_index("idx_claim_cards_speaker_id", "claim_cards", ["speaker_id"])
    op.create_index("idx_claim_cards_created_at", "claim_cards", ["created_at"])
    op.create_index("idx_claim_cards_valid_until", "claim_cards", ["valid_until"])
    op.create_index("idx_claim_cards_asset_scope", "claim_cards", ["asset_scope"], postgresql_using="gin")
    op.create_index("idx_claim_cards_theme_scope", "claim_cards", ["theme_scope"], postgresql_using="gin")

    # claim_raw_source_links
    op.create_table(
        "claim_raw_source_links",
        sa.Column("claim_id", sa.String(26), sa.ForeignKey("claim_cards.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.String(26), sa.ForeignKey("raw_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("link_type", sa.String(50), server_default="extracted_from"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # claim_evidence_links
    op.create_table(
        "claim_evidence_links",
        sa.Column("claim_id", sa.String(26), sa.ForeignKey("claim_cards.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("evidence_span_id", sa.String(26), sa.ForeignKey("evidence_spans.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("relevance_score", sa.Float, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # lifecycle_logs
    op.create_table(
        "lifecycle_logs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("claim_id", sa.String(26), sa.ForeignKey("claim_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=False),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("triggered_by", sa.String(100)),
        sa.Column("evidence_snapshot", _JSONB(), server_default=sa.text("'{}'")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_lifecycle_logs_claim_id", "lifecycle_logs", ["claim_id"])

    # event_cards
    op.create_table(
        "event_cards",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("detail", sa.Text),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(2000)),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_scope", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("theme_scope", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("geographic_scope", _JSONB(), server_default=sa.text("'[]'")),
        sa.Column("importance", sa.Float, server_default="0.5"),
        sa.Column("impact_direction", sa.String(20)),
        sa.Column("time_horizon", sa.String(20)),
        sa.Column("is_surprise", sa.Boolean, server_default="false"),
        sa.Column("market_reaction_notes", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("raw_source_id", sa.String(26), sa.ForeignKey("raw_sources.id", ondelete="SET NULL")),
        sa.Column("extra_data", _JSONB(), server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_event_cards_timestamp", "event_cards", ["event_timestamp"])
    op.create_index("idx_event_cards_importance", "event_cards", ["importance"])
    op.create_index("idx_event_cards_asset_scope", "event_cards", ["asset_scope"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("event_cards")
    op.drop_table("lifecycle_logs")
    op.drop_table("claim_evidence_links")
    op.drop_table("claim_raw_source_links")
    op.drop_table("claim_cards")
    op.drop_table("evidence_spans")
    op.drop_table("speaker_profiles")
    op.drop_table("source_attachments")
    op.drop_table("raw_sources")
