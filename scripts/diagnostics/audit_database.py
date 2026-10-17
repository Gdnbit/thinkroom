#!/usr/bin/env python3
"""Generate a read-only, privacy-preserving audit of the local SQLite database."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.utils.record_classification import (  # noqa: E402
    is_system_chat_title,
    is_system_source_record,
)

VALIDATION_SOURCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"test_decoment",
        r"full[_ -]?chain",
        r"format coverage",
    )
)


def parse_args() -> argparse.Namespace:
    default_database = REPO_ROOT / "apps" / "backend" / "dev.db"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=default_database)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def load_json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _INVALID_JSON


def is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, set, tuple)):
        return bool(value)
    return True


def quantiles(values: Iterable[int | float | None]) -> dict[str, int | float]:
    numbers = sorted(float(value) for value in values if value is not None)
    if not numbers:
        return {"count": 0}

    def percentile(fraction: float) -> float:
        if len(numbers) == 1:
            return numbers[0]
        position = (len(numbers) - 1) * fraction
        low = math.floor(position)
        high = math.ceil(position)
        if low == high:
            return numbers[low]
        return numbers[low] + (numbers[high] - numbers[low]) * (position - low)

    return {
        "count": len(numbers),
        "min": numbers[0],
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": numbers[-1],
        "sum": sum(numbers),
    }


_INVALID_JSON = object()


class DatabaseAudit:
    def __init__(self, database: Path) -> None:
        self.database = database.resolve()
        if not self.database.is_file():
            raise FileNotFoundError(f"Database not found: {self.database}")
        uri = f"file:{self.database.as_posix()}?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("BEGIN")
        self.backend_root = self.database.parent
        self.repo_root = self.backend_root.parent.parent
        self.tables = self._table_names()

    def close(self) -> None:
        self.connection.rollback()
        self.connection.close()

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.connection.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(sql, params)]

    def _table_names(self) -> list[str]:
        return [
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    def _table_counts(self) -> dict[str, int | None]:
        counts: dict[str, int | None] = {}
        for table in self.tables:
            if table == "vec_claims":
                counts[table] = self.scalar("SELECT COUNT(*) FROM vec_claims_map")
                continue
            try:
                counts[table] = self.scalar(f'SELECT COUNT(*) FROM "{table}"')
            except sqlite3.OperationalError:
                counts[table] = None
        return counts

    def _json_quality(self, table: str, columns: Iterable[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for column in columns:
            invalid = 0
            empty = 0
            types: Counter[str] = Counter()
            for row in self.connection.execute(f'SELECT "{column}" FROM "{table}"'):
                value = load_json(row[0])
                if value is _INVALID_JSON:
                    invalid += 1
                    continue
                types[type(value).__name__] += 1
                if not is_nonempty(value):
                    empty += 1
            result[column] = {
                "invalid": invalid,
                "empty": empty,
                "types": dict(types),
            }
        return result

    def _path_exists(self, value: str | None) -> bool:
        if not value:
            return False
        path = Path(value)
        candidates = [path] if path.is_absolute() else [
            self.backend_root / path,
            self.repo_root / path,
        ]
        return any(candidate.exists() for candidate in candidates)

    def _session_completeness(
        self,
        session_ids: set[str],
        messages_by_session: dict[str, list[dict[str, Any]]],
    ) -> dict[str, int]:
        empty = 0
        only_user = 0
        no_assistant = 0
        with_assistant = 0
        for session_id in session_ids:
            messages = messages_by_session.get(session_id, [])
            roles = [message["role"] for message in messages]
            if not messages:
                empty += 1
            if roles and all(role == "user" for role in roles):
                only_user += 1
            if "assistant" in roles:
                with_assistant += 1
            else:
                no_assistant += 1
        return {
            "sessions": len(session_ids),
            "messages": sum(len(messages_by_session.get(key, [])) for key in session_ids),
            "empty_sessions": empty,
            "only_user_sessions": only_user,
            "no_assistant_sessions": no_assistant,
            "with_assistant_sessions": with_assistant,
        }

    def _raw_sources(self) -> dict[str, Any]:
        sources = self.rows(
            "SELECT id,title,source_type,status,language,word_count,raw_text,"
            "article_summary,content_hash,storage_path,processing_error,created_at,"
            "ingested_at,published_at,user_trust_level,collection_reason FROM raw_sources"
        )
        attachments = self.rows(
            "SELECT id,source_id,storage_path,mime_type,file_size_bytes,ocr_text,"
            "transcription_text FROM source_attachments"
        )
        classes: Counter[str] = Counter()
        class_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source in sources:
            probe = " ".join(
                str(source.get(key) or "")
                for key in ("title", "storage_path", "collection_reason")
            )
            if is_system_source_record(
                source_id=source["id"],
                title=source["title"],
                storage_path=source["storage_path"],
            ):
                source_class = "automated_acceptance"
            elif any(pattern.search(probe) for pattern in VALIDATION_SOURCE_PATTERNS):
                source_class = "named_validation_dataset"
            else:
                source_class = "unclassified_or_user"
            classes[source_class] += 1
            class_rows[source_class].append(source)

        duplicate_hashes = self.rows(
            "SELECT content_hash, COUNT(*) AS count FROM raw_sources "
            "WHERE content_hash IS NOT NULL AND trim(content_hash)<>'' "
            "GROUP BY content_hash HAVING COUNT(*)>1"
        )
        length_semantics: Counter[str] = Counter()
        for source in sources:
            raw_text = source["raw_text"] or ""
            stored_count = source["word_count"]
            if stored_count == len(raw_text):
                length_semantics["characters"] += 1
            elif stored_count == len(raw_text.split()):
                length_semantics["whitespace_tokens"] += 1
            else:
                length_semantics["other_or_fixture"] += 1
        return {
            "classes": dict(classes),
            "class_profiles": {
                name: {
                    "rows": len(rows),
                    "status": dict(Counter(row["status"] for row in rows)),
                    "source_type": dict(Counter(row["source_type"] for row in rows)),
                    "word_count_sum": sum(row["word_count"] for row in rows),
                    "word_count_max": max((row["word_count"] for row in rows), default=0),
                    "blank_content_hash": sum(
                        not is_nonempty(row["content_hash"]) for row in rows
                    ),
                }
                for name, rows in class_rows.items()
            },
            "status": dict(Counter(row["status"] for row in sources)),
            "source_type": dict(Counter(row["source_type"] for row in sources)),
            "language": dict(Counter(row["language"] for row in sources)),
            "word_count": quantiles(row["word_count"] for row in sources),
            "raw_text_chars": quantiles(len(row["raw_text"] or "") for row in sources),
            "stored_length_semantics": dict(length_semantics),
            "blank_raw_text": sum(not is_nonempty(row["raw_text"]) for row in sources),
            "blank_article_summary": sum(
                not is_nonempty(row["article_summary"]) for row in sources
            ),
            "blank_content_hash": sum(
                not is_nonempty(row["content_hash"]) for row in sources
            ),
            "processing_error_present": sum(
                is_nonempty(row["processing_error"]) for row in sources
            ),
            "duplicate_content_hash_groups": len(duplicate_hashes),
            "duplicate_content_hash_rows": sum(row["count"] for row in duplicate_hashes),
            "duplicate_exact_text_groups": self.scalar(
                "SELECT COUNT(*) FROM (SELECT raw_text FROM raw_sources "
                "WHERE raw_text IS NOT NULL AND trim(raw_text)<>'' "
                "GROUP BY raw_text HAVING COUNT(*)>1)"
            ),
            "duplicate_title_groups": self.scalar(
                "SELECT COUNT(*) FROM (SELECT title FROM raw_sources "
                "WHERE title IS NOT NULL AND trim(title)<>'' "
                "GROUP BY title HAVING COUNT(*)>1)"
            ),
            "sources_with_claims": self.scalar(
                "SELECT COUNT(DISTINCT source_id) FROM claim_raw_source_links"
            ),
            "completed_without_claims": self.scalar(
                "SELECT COUNT(*) FROM raw_sources r WHERE r.status='completed' "
                "AND NOT EXISTS (SELECT 1 FROM claim_raw_source_links l "
                "WHERE l.source_id=r.id)"
            ),
            "failed_with_claims": self.scalar(
                "SELECT COUNT(*) FROM raw_sources r WHERE r.status='failed' "
                "AND EXISTS (SELECT 1 FROM claim_raw_source_links l "
                "WHERE l.source_id=r.id)"
            ),
            "storage_path_present": sum(is_nonempty(row["storage_path"]) for row in sources),
            "storage_files_missing": sum(
                bool(row["storage_path"]) and not self._path_exists(row["storage_path"])
                for row in sources
            ),
            "attachment_rows": len(attachments),
            "attachment_files_missing": sum(
                bool(row["storage_path"]) and not self._path_exists(row["storage_path"])
                for row in attachments
            ),
            "attachments_with_ocr": sum(is_nonempty(row["ocr_text"]) for row in attachments),
            "attachments_with_transcription": sum(
                is_nonempty(row["transcription_text"]) for row in attachments
            ),
            "replacement_character_titles": sum(
                "\ufffd" in (row["title"] or "") for row in sources
            ),
            "replacement_character_raw_text": sum(
                "\ufffd" in (row["raw_text"] or "") for row in sources
            ),
        }

    def _claims(self) -> dict[str, Any]:
        claims = self.rows("SELECT * FROM claim_cards")
        source_origins: dict[str, str] = {}
        for source in self.rows(
            "SELECT id,title,storage_path,collection_reason FROM raw_sources"
        ):
            source_origins[source["id"]] = (
                "automated_acceptance"
                if is_system_source_record(
                    source_id=source["id"],
                    title=source["title"],
                    storage_path=source["storage_path"],
                )
                else "unclassified_or_user"
            )
        claim_source_origins: dict[str, set[str]] = defaultdict(set)
        for link in self.rows("SELECT claim_id,source_id FROM claim_raw_source_links"):
            claim_source_origins[link["claim_id"]].add(
                source_origins.get(link["source_id"], "missing_source")
            )
        claim_origin_profile: Counter[str] = Counter()
        for claim in claims:
            origins = claim_source_origins.get(claim["id"], set())
            if "unclassified_or_user" in origins:
                claim_origin_profile["has_unclassified_or_user_source"] += 1
            elif origins == {"automated_acceptance"}:
                claim_origin_profile["automated_only"] += 1
            else:
                claim_origin_profile["other_or_missing"] += 1
        json_columns = (
            "asset_scope",
            "theme_scope",
            "geographic_scope",
            "conditions",
            "invalidation_conditions",
            "reasoning_basis",
        )
        field_quality = {
            "blank_summary": sum(not is_nonempty(row["summary"]) for row in claims),
            "blank_full_quote": sum(not is_nonempty(row["full_quote"]) for row in claims),
            "blank_contextual_text": sum(
                not is_nonempty(row["contextual_text"]) for row in claims
            ),
            "missing_time_horizon": sum(
                not is_nonempty(row["time_horizon"]) for row in claims
            ),
            "time_sensitive_without_valid_from": sum(
                bool(row["time_sensitive"]) and not row["valid_from"] for row in claims
            ),
            "time_sensitive_without_valid_until_or_hint": sum(
                bool(row["time_sensitive"])
                and not row["valid_until"]
                and not is_nonempty(row["expiry_hint"])
                for row in claims
            ),
            "confidence_out_of_range": sum(
                row["confidence"] is None or not 0 <= row["confidence"] <= 1
                for row in claims
            ),
            "trust_weight_out_of_range": sum(
                row["source_trust_weight"] is None
                or not 0 <= row["source_trust_weight"] <= 1
                for row in claims
            ),
        }
        for column in json_columns:
            field_quality[f"nonempty_{column}"] = sum(
                is_nonempty(load_json(row[column])) for row in claims
            )
        return {
            "total": len(claims),
            "status": dict(Counter(row["status"] for row in claims)),
            "claim_type": dict(Counter(row["claim_type"] for row in claims)),
            "stance": dict(Counter(row["stance"] for row in claims)),
            "time_horizon": dict(Counter(str(row["time_horizon"]) for row in claims)),
            "origin_profile": dict(claim_origin_profile),
            "json_quality": self._json_quality("claim_cards", json_columns),
            "field_quality": field_quality,
            "with_source_links": self.scalar(
                "SELECT COUNT(DISTINCT claim_id) FROM claim_raw_source_links"
            ),
            "with_evidence_links": self.scalar(
                "SELECT COUNT(DISTINCT claim_id) FROM claim_evidence_links"
            ),
            "with_vectors": self.scalar(
                "SELECT COUNT(DISTINCT v.claim_id) FROM vec_claims_map v "
                "JOIN claim_cards c ON c.id=v.claim_id"
            ),
            "active_with_vectors": self.scalar(
                "SELECT COUNT(DISTINCT v.claim_id) FROM vec_claims_map v "
                "JOIN claim_cards c ON c.id=v.claim_id WHERE c.status='active'"
            ),
            "vector_map_rows": self.scalar("SELECT COUNT(*) FROM vec_claims_map"),
            "embedding_column_nonblank": self.scalar(
                "SELECT COUNT(*) FROM claim_cards WHERE embedding IS NOT NULL "
                "AND trim(embedding)<>''"
            ),
            "orphan_vector_maps": self.scalar(
                "SELECT COUNT(*) FROM vec_claims_map v LEFT JOIN claim_cards c "
                "ON c.id=v.claim_id WHERE c.id IS NULL"
            ),
            "duplicate_vector_claims": self.scalar(
                "SELECT COUNT(*) FROM (SELECT claim_id FROM vec_claims_map "
                "GROUP BY claim_id HAVING COUNT(*)>1)"
            ),
            "evidence_source_mismatch": self.scalar(
                "SELECT COUNT(*) FROM claim_evidence_links cel "
                "JOIN evidence_spans es ON es.id=cel.evidence_span_id "
                "WHERE NOT EXISTS (SELECT 1 FROM claim_raw_source_links crsl "
                "WHERE crsl.claim_id=cel.claim_id AND crsl.source_id=es.source_id)"
            ),
            "duplicate_summary_groups": self.scalar(
                "SELECT COUNT(*) FROM (SELECT lower(trim(summary)) AS value "
                "FROM claim_cards WHERE trim(summary)<>'' GROUP BY value "
                "HAVING COUNT(*)>1)"
            ),
            "duplicate_summary_rows": self.scalar(
                "SELECT COALESCE(SUM(count),0) FROM (SELECT COUNT(*) AS count "
                "FROM claim_cards WHERE trim(summary)<>'' "
                "GROUP BY lower(trim(summary)) HAVING COUNT(*)>1)"
            ),
        }

    def _chat(self) -> dict[str, Any]:
        sessions = self.rows("SELECT * FROM chat_sessions")
        messages = self.rows(
            "SELECT id,session_id,role,content,payload,created_at FROM chat_messages"
        )
        system_ids = {
            row["id"]
            for row in sessions
            if is_system_chat_title(row["title"])
        }
        user_ids = {row["id"] for row in sessions} - system_ids
        by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for message in messages:
            by_session[message["session_id"]].append(message)

        citations: Counter[str] = Counter()
        quality_reports: Counter[str] = Counter()
        route_fingerprints: Counter[str] = Counter()
        citation_targets: dict[str, Counter[str]] = {
            "all": Counter(),
            "system_test": Counter(),
            "user_visible": Counter(),
        }
        claim_ids = set(
            row[0] for row in self.connection.execute("SELECT id FROM claim_cards")
        )
        source_ids = set(
            row[0] for row in self.connection.execute("SELECT id FROM raw_sources")
        )
        evidence_span_ids = set(
            row[0] for row in self.connection.execute("SELECT id FROM evidence_spans")
        )
        for message in messages:
            if message["role"] != "assistant":
                continue
            payload = load_json(message["payload"])
            if not isinstance(payload, dict):
                continue
            value = payload.get("citations")
            citations["with_citations" if isinstance(value, list) and value else "without_citations"] += 1
            bucket = "system_test" if message["session_id"] in system_ids else "user_visible"
            if isinstance(value, list):
                for citation in value:
                    if not isinstance(citation, dict):
                        continue
                    claim_id = citation.get("claim_id")
                    source_id = citation.get("source_id")
                    evidence_span_id = citation.get("evidence_span_id")
                    for target in ("all", bucket):
                        stats = citation_targets[target]
                        stats["citations"] += 1
                        stats["claim_id_present"] += bool(claim_id)
                        stats["claim_id_missing_parent"] += bool(
                            claim_id and claim_id not in claim_ids
                        )
                        stats["source_id_present"] += bool(source_id)
                        stats["source_id_missing_parent"] += bool(
                            source_id and source_id not in source_ids
                        )
                        stats["evidence_span_id_present"] += bool(evidence_span_id)
                        stats["evidence_span_id_missing_parent"] += bool(
                            evidence_span_id and evidence_span_id not in evidence_span_ids
                        )
            quality = payload.get("quality_report")
            if isinstance(quality, dict):
                quality_reports["with_quality_report"] += 1
                quality_reports[
                    "decision_ready_true"
                    if quality.get("decision_ready") is True
                    else "decision_ready_not_true"
                ] += 1
                if quality.get("insufficient_evidence_reason"):
                    quality_reports["with_insufficiency_reason"] += 1
            else:
                quality_reports["without_quality_report"] += 1
            route = payload.get("route_identity")
            if isinstance(route, dict) and route.get("fingerprint"):
                route_fingerprints[str(route["fingerprint"])] += 1

        return {
            "system_test": self._session_completeness(system_ids, by_session),
            "user_visible": self._session_completeness(user_ids, by_session),
            "quality": {
                "blank_message_content": sum(
                    not is_nonempty(row["content"]) for row in messages
                ),
                "orphan_messages": self.scalar(
                    "SELECT COUNT(*) FROM chat_messages m LEFT JOIN chat_sessions s "
                    "ON s.id=m.session_id WHERE s.id IS NULL"
                ),
                "replacement_character_titles": sum(
                    "\ufffd" in (row["title"] or "") for row in sessions
                ),
                "replacement_character_messages": sum(
                    "\ufffd" in (row["content"] or "") for row in messages
                ),
                "roles": dict(Counter(row["role"] for row in messages)),
                "modes": dict(Counter(row["mode"] for row in sessions)),
            },
            "payload_json": self._json_quality("chat_messages", ("payload",)),
            "assistant_citations": dict(citations),
            "citation_target_validity": {
                key: dict(value) for key, value in citation_targets.items()
            },
            "assistant_quality_reports": dict(quality_reports),
            "route_fingerprints": dict(route_fingerprints),
        }

    def _briefings(self) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM briefing_cards")
        claim_ids = set(
            row[0] for row in self.connection.execute("SELECT id FROM claim_cards")
        )
        references: list[str] = []
        for row in rows:
            value = load_json(row["source_claim_ids"])
            if isinstance(value, list):
                references.extend(str(item) for item in value)
        return {
            "statuses": dict(Counter(row["status"] for row in rows)),
            "committee_levels": dict(Counter(row["committee_level"] for row in rows)),
            "models": dict(Counter(row["model_used"] or "(none)" for row in rows)),
            "with_source_claim_ids": sum(
                is_nonempty(load_json(row["source_claim_ids"])) for row in rows
            ),
            "with_source_event_ids": sum(
                is_nonempty(load_json(row["source_event_ids"])) for row in rows
            ),
            "with_setup_id": sum(is_nonempty(row["setup_id"]) for row in rows),
            "blank_executive_summary": sum(
                not is_nonempty(row["executive_summary"]) for row in rows
            ),
            "blank_recommended_action": sum(
                not is_nonempty(row["recommended_action"]) for row in rows
            ),
            "blank_summary_without_model": sum(
                not is_nonempty(row["executive_summary"])
                and not is_nonempty(row["model_used"])
                for row in rows
            ),
            "blank_summary_without_claims": sum(
                not is_nonempty(row["executive_summary"])
                and not is_nonempty(load_json(row["source_claim_ids"]))
                for row in rows
            ),
            "dedicated_evidence_link_rows": self.scalar(
                "SELECT COUNT(*) FROM briefing_evidence_links"
            ),
            "claim_references_total": len(references),
            "claim_references_missing_parent": sum(
                reference not in claim_ids for reference in references
            ),
            "json_quality": self._json_quality(
                "briefing_cards", ("source_claim_ids", "source_event_ids")
            ),
        }

    def _snapshots(self) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM decision_snapshots")
        claim_ids = set(
            row[0] for row in self.connection.execute("SELECT id FROM claim_cards")
        )
        classes: Counter[str] = Counter()
        claim_reference_count = 0
        missing_claim_references = 0
        nonempty_claims = 0
        markers = (
            "runtime smoke",
            "main-flow-smoke",
            "mvp acceptance",
            "mvp_acceptance",
            "scope acceptance",
            "scope_acceptance",
            "codex-pass",
            "pass smoke",
            "ui-pass",
        )
        for row in rows:
            coverage = load_json(row["data_coverage"])
            if not isinstance(coverage, dict):
                coverage = {}
            trigger = str(row["trigger_type"] or "").lower()
            origin = str(coverage.get("record_origin") or "").lower()
            visibility = str(coverage.get("visibility_scope") or "").lower()
            text = " ".join(
                str(row.get(key) or "")
                for key in ("question", "synthesis_summary", "trigger_type", "trigger_id")
            ).lower()
            if trigger == "chat" or origin == "chat_user_confirmed":
                classes["user_primary"] += 1
            elif (
                visibility == "system_test"
                or origin == "system_test"
                or any(marker in text for marker in markers)
            ):
                classes["system_test"] += 1
            else:
                classes["workflow_history"] += 1

            claims = load_json(row["claims_snapshot"])
            if not isinstance(claims, list):
                continue
            if claims:
                nonempty_claims += 1
            for item in claims:
                claim_reference_count += 1
                claim_id = item.get("id") if isinstance(item, dict) else item
                if claim_id and str(claim_id) not in claim_ids:
                    missing_claim_references += 1

        return {
            "classes": dict(classes),
            "trigger_types": dict(Counter(row["trigger_type"] for row in rows)),
            "replay_status": dict(Counter(row["replay_status"] for row in rows)),
            "with_outcome_direction": sum(
                is_nonempty(row["outcome_direction"]) for row in rows
            ),
            "with_outcome_observed_at": sum(
                row["outcome_observed_at"] is not None for row in rows
            ),
            "with_nonempty_claims": nonempty_claims,
            "claim_references_total": claim_reference_count,
            "claim_references_missing_parent": missing_claim_references,
            "duplicate_trigger_pairs": self.scalar(
                "SELECT COUNT(*) FROM (SELECT trigger_type,trigger_id "
                "FROM decision_snapshots WHERE trigger_id IS NOT NULL "
                "GROUP BY trigger_type,trigger_id HAVING COUNT(*)>1)"
            ),
            "json_quality": self._json_quality(
                "decision_snapshots", ("claims_snapshot", "data_coverage")
            ),
        }

    def _outcomes(self) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM outcome_records")
        return {
            "records": len(rows),
            "types": dict(Counter(row["outcome_type"] for row in rows)),
            "correctness": dict(Counter(str(row["is_correct"]) for row in rows)),
            "orphan_briefing_refs": self.scalar(
                "SELECT COUNT(*) FROM outcome_records o LEFT JOIN briefing_cards b "
                "ON b.id=o.briefing_id WHERE o.briefing_id IS NOT NULL AND b.id IS NULL"
            ),
            "orphan_claim_refs": self.scalar(
                "SELECT COUNT(*) FROM outcome_records o LEFT JOIN claim_cards c "
                "ON c.id=o.claim_id WHERE o.claim_id IS NOT NULL AND c.id IS NULL"
            ),
            "orphan_setup_refs": self.scalar(
                "SELECT COUNT(*) FROM outcome_records o LEFT JOIN setup_cards s "
                "ON s.id=o.setup_id WHERE o.setup_id IS NOT NULL AND s.id IS NULL"
            ),
        }

    def _committee(self) -> dict[str, Any]:
        rows = self.rows("SELECT * FROM debate_records")
        return {
            "statuses": dict(Counter(row["status"] for row in rows)),
            "levels": dict(Counter(row["committee_level"] for row in rows)),
            "with_final_adjudication": self.scalar(
                "SELECT COUNT(DISTINCT debate_id) FROM final_adjudications"
            ),
            "without_final_adjudication": self.scalar(
                "SELECT COUNT(*) FROM debate_records d WHERE NOT EXISTS "
                "(SELECT 1 FROM final_adjudications f WHERE f.debate_id=d.id)"
            ),
            "rounds_per_debate": quantiles(
                row[0]
                for row in self.connection.execute(
                    "SELECT COUNT(*) FROM debate_rounds GROUP BY debate_id"
                )
            ),
            "mock_rounds": self.scalar(
                "SELECT COUNT(*) FROM debate_rounds WHERE is_mock=1"
            ),
            "real_rounds": self.scalar(
                "SELECT COUNT(*) FROM debate_rounds WHERE is_mock=0"
            ),
            "mock_adjudications": self.scalar(
                "SELECT COUNT(*) FROM final_adjudications WHERE is_mock=1"
            ),
            "real_adjudications": self.scalar(
                "SELECT COUNT(*) FROM final_adjudications WHERE is_mock=0"
            ),
        }

    def _jobs(self) -> tuple[dict[str, Any], dict[str, Any]]:
        rows = self.rows("SELECT * FROM job_runs")
        jobs = {
            "statuses": dict(Counter(row["status"] for row in rows)),
            "types": dict(Counter(row["job_type"] for row in rows)),
            "triggered_by": dict(Counter(row["triggered_by"] for row in rows)),
            "duration_ms": quantiles(row["duration_ms"] for row in rows),
            "with_error_message": sum(
                is_nonempty(row["error_message"]) for row in rows
            ),
            "with_session_id": sum(is_nonempty(row["session_id"]) for row in rows),
            "invalid_json": self._json_quality(
                "job_runs", ("input_params", "result_summary", "source_ids", "object_scope")
            ),
            "running_older_than_1h": self.scalar(
                "SELECT COUNT(*) FROM job_runs WHERE status='running' "
                "AND datetime(created_at) < datetime('now','-1 hour')"
            ),
            "pending_older_than_1h": self.scalar(
                "SELECT COUNT(*) FROM job_runs WHERE status='pending' "
                "AND datetime(created_at) < datetime('now','-1 hour')"
            ),
            "jobs_without_processing_traces": self.scalar(
                "SELECT COUNT(*) FROM job_runs j WHERE NOT EXISTS "
                "(SELECT 1 FROM processing_traces p WHERE p.job_id=j.id)"
            ),
            "ingestion_jobs_without_processing_traces": self.scalar(
                "SELECT COUNT(*) FROM job_runs j WHERE j.job_type='ingestion' "
                "AND NOT EXISTS (SELECT 1 FROM processing_traces p WHERE p.job_id=j.id)"
            ),
        }
        traces = {
            "statuses": dict(
                Counter(
                    row[0]
                    for row in self.connection.execute(
                        "SELECT status FROM processing_traces"
                    )
                )
            ),
            "step_names": dict(
                Counter(
                    row[0]
                    for row in self.connection.execute(
                        "SELECT step_name FROM processing_traces"
                    )
                )
            ),
            "orphan_jobs": self.scalar(
                "SELECT COUNT(*) FROM processing_traces p LEFT JOIN job_runs j "
                "ON j.id=p.job_id WHERE p.job_id IS NOT NULL AND j.id IS NULL"
            ),
            "orphan_sources": self.scalar(
                "SELECT COUNT(*) FROM processing_traces p LEFT JOIN raw_sources s "
                "ON s.id=p.source_id WHERE s.id IS NULL"
            ),
            "distinct_missing_source_ids": self.scalar(
                "SELECT COUNT(DISTINCT p.source_id) FROM processing_traces p "
                "LEFT JOIN raw_sources s ON s.id=p.source_id WHERE s.id IS NULL"
            ),
            "duplicate_job_source_step": self.scalar(
                "SELECT COUNT(*) FROM (SELECT job_id,source_id,step_index,COUNT(*) AS count "
                "FROM processing_traces GROUP BY job_id,source_id,step_index "
                "HAVING count>1)"
            ),
        }
        return jobs, traces

    def _decision_traces(self) -> dict[str, Any]:
        return {
            "statuses": dict(
                Counter(
                    row[0]
                    for row in self.connection.execute(
                        "SELECT status FROM decision_trace_events"
                    )
                )
            ),
            "event_types": dict(
                Counter(
                    row[0]
                    for row in self.connection.execute(
                        "SELECT event_type FROM decision_trace_events"
                    )
                )
            ),
            "events_total": self.scalar("SELECT COUNT(*) FROM decision_trace_events"),
            "with_source_links": self.scalar(
                "SELECT COUNT(DISTINCT trace_event_id) FROM decision_trace_source_links"
            ),
            "orphan_trace_links": self.scalar(
                "SELECT COUNT(*) FROM decision_trace_source_links l "
                "LEFT JOIN decision_trace_events e ON e.id=l.trace_event_id "
                "WHERE e.id IS NULL"
            ),
            "source_links_to_missing_raw_source": self.scalar(
                "SELECT COUNT(*) FROM decision_trace_source_links l "
                "LEFT JOIN raw_sources s ON s.id=l.source_id WHERE s.id IS NULL"
            ),
            "source_id_has_no_foreign_key": True,
        }

    def _configuration(self, counts: dict[str, int | None]) -> dict[str, Any]:
        secret_classes: Counter[str] = Counter()
        for row in self.connection.execute("SELECT api_key FROM key_groups"):
            value = str(row[0] or "").strip()
            lowered = value.lower()
            if not value:
                secret_classes["blank"] += 1
            elif lowered.startswith(("env:", "sops:", "enc[")) or value.startswith("${"):
                secret_classes["reference_or_encrypted"] += 1
            elif (
                lowered in {"changeme", "placeholder", "test", "dummy", "none"}
                or "your_" in lowered
            ):
                secret_classes["placeholder"] += 1
            else:
                secret_classes["plaintext_like"] += 1
        return {
            "key_group_rows": counts.get("key_groups", 0),
            "key_material_classification": dict(secret_classes),
            "model_registry_rows": counts.get("model_registry", 0),
            "model_registry_available": self.scalar(
                "SELECT COUNT(*) FROM model_registry WHERE is_available=1"
            ),
            "model_registry_distinct_models": self.scalar(
                "SELECT COUNT(DISTINCT model) FROM model_registry"
            ),
            "alembic_version_rows": counts.get("alembic_version", 0),
        }

    def _foreign_key_index_gaps(self) -> list[dict[str, str]]:
        gaps: list[dict[str, str]] = []
        for table in self.tables:
            if table == "vec_claims" or table.startswith("vec_claims_"):
                continue
            try:
                foreign_keys = self.rows(f'PRAGMA foreign_key_list("{table}")')
            except sqlite3.OperationalError:
                continue
            indexed_first_columns: set[str] = set()
            for index in self.connection.execute(f'PRAGMA index_list("{table}")'):
                columns = list(
                    self.connection.execute(f'PRAGMA index_info("{index[1]}")')
                )
                if columns:
                    indexed_first_columns.add(columns[0][2])
            for foreign_key in foreign_keys:
                if foreign_key["from"] not in indexed_first_columns:
                    gaps.append(
                        {
                            "table": table,
                            "column": foreign_key["from"],
                            "parent": foreign_key["table"],
                        }
                    )
        return gaps

    def _time_profile(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in (
            "raw_sources",
            "claim_cards",
            "chat_sessions",
            "briefing_cards",
            "decision_snapshots",
            "outcome_records",
            "job_runs",
            "debate_records",
            "market_snapshots",
            "source_health_logs",
        ):
            result[table] = {
                "min": self.scalar(f'SELECT MIN(created_at) FROM "{table}"'),
                "max": self.scalar(f'SELECT MAX(created_at) FROM "{table}"'),
                "recent_days": self.rows(
                    f'SELECT substr(created_at,1,10) AS day, COUNT(*) AS count '
                    f'FROM "{table}" WHERE created_at IS NOT NULL '
                    f'GROUP BY day ORDER BY day DESC LIMIT 14'
                ),
            }
        return result

    def run(self) -> dict[str, Any]:
        counts = self._table_counts()
        jobs, processing_traces = self._jobs()
        return {
            "audit_meta": {
                "database": str(self.database),
                "read_only": True,
                "transaction_snapshot": True,
                "audited_at": datetime.now(timezone.utc).isoformat(),
                "size_bytes": self.database.stat().st_size,
                "table_count": len(self.tables),
                "journal_mode": self.scalar("PRAGMA journal_mode"),
                "page_size": self.scalar("PRAGMA page_size"),
            },
            "integrity": {
                "integrity_check": [
                    row[0] for row in self.connection.execute("PRAGMA integrity_check")
                ],
                "foreign_key_violation_count": self.scalar(
                    "SELECT COUNT(*) FROM pragma_foreign_key_check"
                ),
                "foreign_key_violations": self.rows(
                    "SELECT * FROM pragma_foreign_key_check"
                ),
            },
            "table_counts": counts,
            "zones": {
                "v0_1_core": [
                    "raw_sources",
                    "source_attachments",
                    "claim_cards",
                    "claim_raw_source_links",
                    "evidence_spans",
                    "claim_evidence_links",
                    "chat_sessions",
                    "chat_messages",
                    "briefing_cards",
                    "briefing_evidence_links",
                    "decision_snapshots",
                    "outcome_records",
                ],
                "operations": [
                    "job_runs",
                    "processing_traces",
                    "decision_trace_events",
                    "decision_trace_source_links",
                    "decision_trace_eval_reviews",
                ],
                "optional_committee": [
                    "debate_records",
                    "debate_rounds",
                    "final_adjudications",
                ],
                "legacy_or_auxiliary": [
                    "event_cards",
                    "flow_signal_cards",
                    "market_snapshots",
                    "market_state_cards",
                    "fund_profiles",
                    "fund_estimate_snapshots",
                    "source_health_logs",
                    "speaker_profiles",
                    "strategy_cards",
                    "strategy_source_links",
                    "setup_cards",
                    "lifecycle_logs",
                    "watchlists",
                    "watchlist_items",
                    "user_overrides",
                ],
                "configuration": [
                    "key_groups",
                    "model_registry",
                    "alembic_version",
                    "vec_claims",
                ],
            },
            "raw_sources": self._raw_sources(),
            "claims": self._claims(),
            "chat": self._chat(),
            "briefings": self._briefings(),
            "snapshots": self._snapshots(),
            "outcomes": self._outcomes(),
            "committee": self._committee(),
            "jobs": jobs,
            "processing_traces": processing_traces,
            "decision_traces": self._decision_traces(),
            "configuration": self._configuration(counts),
            "foreign_key_index_gaps": self._foreign_key_index_gaps(),
            "time_profile": self._time_profile(),
        }


def main() -> int:
    args = parse_args()
    audit = DatabaseAudit(args.database)
    try:
        report = audit.run()
    finally:
        audit.close()
    rendered = json.dumps(
        report,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
