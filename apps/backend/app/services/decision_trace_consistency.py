"""Consistency checks for decision trace source links."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink


def _source_pairs_sql(dialect: str) -> str:
    if dialect == "sqlite":
        return """
            SELECT
                e.id AS trace_event_id,
                CAST(json_each.value AS TEXT) AS source_id
            FROM decision_trace_events AS e, json_each(e.source_ids)
            WHERE json_valid(e.source_ids)
              AND json_each.value IS NOT NULL
              AND CAST(json_each.value AS TEXT) != ''
            GROUP BY e.id, CAST(json_each.value AS TEXT)
        """

    if dialect == "postgresql":
        return """
            SELECT
                e.id AS trace_event_id,
                source.value AS source_id
            FROM decision_trace_events AS e
            CROSS JOIN LATERAL jsonb_array_elements_text(e.source_ids) AS source(value)
            WHERE source.value IS NOT NULL
              AND source.value != ''
            GROUP BY e.id, source.value
        """

    raise ValueError(f"Unsupported dialect for trace consistency check: {dialect}")


def get_trace_source_link_consistency(db: Session, *, sample_limit: int = 20) -> dict[str, Any]:
    """Return read-only consistency stats for trace JSON sources and link rows."""

    dialect = db.bind.dialect.name
    source_pairs_sql = _source_pairs_sql(dialect)

    trace_event_count = db.query(DecisionTraceEvent).count()
    link_count = db.query(DecisionTraceSourceLink).count()

    event_source_pair_count = db.execute(text(f"""
        WITH json_sources AS (
            {source_pairs_sql}
        )
        SELECT COUNT(*) FROM json_sources
    """)).scalar_one()

    missing_link_count = db.execute(text(f"""
        WITH json_sources AS (
            {source_pairs_sql}
        )
        SELECT COUNT(*)
        FROM json_sources AS s
        LEFT JOIN decision_trace_source_links AS l
          ON l.trace_event_id = s.trace_event_id
         AND l.source_id = s.source_id
        WHERE l.trace_event_id IS NULL
    """)).scalar_one()

    orphan_link_count = db.execute(text("""
        SELECT COUNT(*)
        FROM decision_trace_source_links AS l
        LEFT JOIN decision_trace_events AS e
          ON e.id = l.trace_event_id
        WHERE e.id IS NULL
    """)).scalar_one()

    sample_missing_links = [
        dict(row)
        for row in db.execute(text(f"""
            WITH json_sources AS (
                {source_pairs_sql}
            )
            SELECT s.trace_event_id, s.source_id
            FROM json_sources AS s
            LEFT JOIN decision_trace_source_links AS l
              ON l.trace_event_id = s.trace_event_id
             AND l.source_id = s.source_id
            WHERE l.trace_event_id IS NULL
            ORDER BY s.trace_event_id, s.source_id
            LIMIT :sample_limit
        """), {"sample_limit": sample_limit}).mappings().all()
    ]

    sample_orphan_links = [
        dict(row)
        for row in db.execute(text("""
            SELECT l.trace_event_id, l.source_id
            FROM decision_trace_source_links AS l
            LEFT JOIN decision_trace_events AS e
              ON e.id = l.trace_event_id
            WHERE e.id IS NULL
            ORDER BY l.trace_event_id, l.source_id
            LIMIT :sample_limit
        """), {"sample_limit": sample_limit}).mappings().all()
    ]

    healthy = missing_link_count == 0 and orphan_link_count == 0
    return {
        "status": "ok" if healthy else "warning",
        "dialect": dialect,
        "trace_event_count": trace_event_count,
        "event_source_pair_count": event_source_pair_count,
        "link_count": link_count,
        "missing_link_count": missing_link_count,
        "orphan_link_count": orphan_link_count,
        "sample_missing_links": sample_missing_links,
        "sample_orphan_links": sample_orphan_links,
    }
