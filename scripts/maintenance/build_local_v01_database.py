#!/usr/bin/env python3
"""Build a non-destructive local v0.1 user database from a retained SQLite snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
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


VECTOR_INTERNAL_TABLES = {
    "vec_claims",
    "vec_claims_chunks",
    "vec_claims_info",
    "vec_claims_rowids",
    "vec_claims_vector_chunks00",
}

AUXILIARY_TABLES_TO_CLEAR = {
    "decision_trace_eval_reviews",
    "flow_signal_cards",
    "fund_estimate_snapshots",
    "fund_profiles",
    "key_groups",
    "market_snapshots",
    "market_state_cards",
    "source_health_logs",
    "watchlist_items",
    "watchlists",
}

ENTITY_TABLES = {
    "briefing_cards",
    "briefing_evidence_links",
    "chat_messages",
    "chat_sessions",
    "claim_cards",
    "debate_records",
    "debate_rounds",
    "decision_snapshots",
    "decision_trace_events",
    "event_cards",
    "evidence_spans",
    "final_adjudications",
    "job_runs",
    "lifecycle_logs",
    "outcome_records",
    "processing_traces",
    "raw_sources",
    "setup_cards",
    "source_attachments",
    "speaker_profiles",
    "strategy_cards",
    "user_overrides",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=REPO_ROOT / "apps" / "backend" / "dev.db",
        help="Retained source database. It is opened read-only and never modified.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / ".runtime" / "data" / "user" / "local-v0.1.db",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--backup-database", type=Path)
    parser.add_argument("--backup-uploads", type=Path)
    return parser.parse_args()


def load_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        display_path = resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        display_path = resolved.as_posix()
    return {
        "path": display_path,
        "bytes": resolved.stat().st_size,
        "sha256": hash_file(resolved),
    }


def git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class Selection:
    """Compute the minimum provenance-closed graph needed by the local v0.1 flow."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        self.rows = {
            table: self._read_rows(table)
            for table in sorted(self.tables - VECTOR_INTERNAL_TABLES)
        }
        self.ids: dict[str, set[str]] = {}
        self.id_to_tables: dict[str, set[str]] = defaultdict(set)
        for table, rows in self.rows.items():
            table_ids = {str(row["id"]) for row in rows if "id" in row.keys() and row["id"] is not None}
            if table_ids:
                self.ids[table] = table_ids
                for record_id in table_ids:
                    self.id_to_tables[record_id].add(table)
        self.keep: dict[str, set[Any]] = defaultdict(set)

    def _read_rows(self, table: str) -> list[sqlite3.Row]:
        try:
            return list(self.connection.execute(f'SELECT * FROM "{table}"'))
        except sqlite3.DatabaseError:
            return []

    def _add_known_id(self, value: Any) -> None:
        if not isinstance(value, str):
            return
        for table in self.id_to_tables.get(value, ()):
            self.keep[table].add(value)

    def _collect_known_ids(self, value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                self._collect_known_ids(child)
        elif isinstance(value, list):
            for child in value:
                self._collect_known_ids(child)
        else:
            self._add_known_id(value)

    def _initialise(self) -> None:
        self.keep["chat_sessions"] = {
            row["id"]
            for row in self.rows.get("chat_sessions", [])
            if not is_system_chat_title(row["title"])
        }
        self.keep["raw_sources"] = {
            row["id"]
            for row in self.rows.get("raw_sources", [])
            if not is_system_source_record(
                source_id=row["id"],
                title=row["title"],
                storage_path=row["storage_path"],
            )
        }
        self.keep["outcome_records"] = set(self.ids.get("outcome_records", set()))

        for row in self.rows.get("decision_snapshots", []):
            coverage = load_json(row["data_coverage"], {})
            trigger_type = str(row["trigger_type"] or "").lower()
            origin = str(coverage.get("record_origin") or "").lower()
            if trigger_type == "chat" or origin == "chat_user_confirmed":
                self.keep["decision_snapshots"].add(row["id"])

        for row in self.rows.get("chat_messages", []):
            if row["session_id"] not in self.keep["chat_sessions"]:
                continue
            self.keep["chat_messages"].add(row["id"])
            self._collect_known_ids(load_json(row["payload"], {}))

    def _expand_once(self) -> None:
        for row in self.rows.get("claim_raw_source_links", []):
            if row["source_id"] in self.keep["raw_sources"]:
                self.keep["claim_cards"].add(row["claim_id"])
            if row["claim_id"] in self.keep["claim_cards"]:
                self.keep["raw_sources"].add(row["source_id"])

        for row in self.rows.get("claim_cards", []):
            if row["id"] not in self.keep["claim_cards"]:
                continue
            self._add_known_id(row["parent_claim_id"])
            self._add_known_id(row["speaker_id"])

        for row in self.rows.get("claim_evidence_links", []):
            if row["claim_id"] in self.keep["claim_cards"]:
                self.keep["evidence_spans"].add(row["evidence_span_id"])
        for row in self.rows.get("evidence_spans", []):
            if row["source_id"] in self.keep["raw_sources"]:
                self.keep["evidence_spans"].add(row["id"])
            if row["id"] in self.keep["evidence_spans"]:
                self.keep["raw_sources"].add(row["source_id"])

        for row in self.rows.get("briefing_cards", []):
            if row["id"] not in self.keep["briefing_cards"]:
                continue
            self._add_known_id(row["setup_id"])
            self._collect_known_ids(load_json(row["source_claim_ids"], []))
            self._collect_known_ids(load_json(row["source_event_ids"], []))

        for row in self.rows.get("debate_records", []):
            if row["id"] not in self.keep["debate_records"]:
                continue
            self._add_known_id(row["briefing_id"])
            for column in ("context_claims", "context_events", "context_market", "model_by_seat"):
                self._collect_known_ids(load_json(row[column], None))

        retained_triggers = set().union(
            self.keep["chat_sessions"],
            self.keep["chat_messages"],
            self.keep["briefing_cards"],
            self.keep["debate_records"],
        )
        for row in self.rows.get("decision_snapshots", []):
            if row["trigger_id"] in retained_triggers:
                self.keep["decision_snapshots"].add(row["id"])
            if row["id"] in self.keep["decision_snapshots"]:
                self._collect_known_ids(load_json(row["claims_snapshot"], []))

        for row in self.rows.get("outcome_records", []):
            if row["id"] not in self.keep["outcome_records"]:
                continue
            for column in ("briefing_id", "claim_id", "setup_id"):
                self._add_known_id(row[column])

        for row in self.rows.get("setup_cards", []):
            if row["id"] not in self.keep["setup_cards"]:
                continue
            self._add_known_id(row["strategy_id"])
            self._collect_known_ids(load_json(row["supporting_claim_ids"], []))
            self._collect_known_ids(load_json(row["supporting_event_ids"], []))

        for row in self.rows.get("event_cards", []):
            if row["id"] in self.keep["event_cards"]:
                self._add_known_id(row["source_id"])

        retained_objects = set().union(
            self.keep["raw_sources"],
            self.keep["claim_cards"],
            self.keep["briefing_cards"],
            self.keep["debate_records"],
            self.keep["decision_snapshots"],
            self.keep["outcome_records"],
            self.keep["setup_cards"],
        )
        for row in self.rows.get("job_runs", []):
            source_ids = load_json(row["source_ids"], []) or []
            if (
                row["session_id"] in self.keep["chat_sessions"]
                or row["related_object_id"] in retained_objects
                or any(source_id in self.keep["raw_sources"] for source_id in source_ids)
            ):
                self.keep["job_runs"].add(row["id"])

        for row in self.rows.get("processing_traces", []):
            if row["source_id"] in self.keep["raw_sources"] or row["job_id"] in self.keep["job_runs"]:
                self.keep["processing_traces"].add(row["id"])

        for row in self.rows.get("decision_trace_events", []):
            source_ids = load_json(row["source_ids"], []) or []
            if (
                row["session_id"] in self.keep["chat_sessions"]
                or row["job_id"] in self.keep["job_runs"]
                or row["related_object_id"] in retained_objects
                or any(source_id in self.keep["raw_sources"] for source_id in source_ids)
            ):
                self.keep["decision_trace_events"].add(row["id"])

    def _add_dependents(self) -> None:
        for row in self.rows.get("source_attachments", []):
            if row["source_id"] in self.keep["raw_sources"]:
                self.keep["source_attachments"].add(row["id"])
        for row in self.rows.get("lifecycle_logs", []):
            if row["claim_id"] in self.keep["claim_cards"]:
                self.keep["lifecycle_logs"].add(row["id"])
        for row in self.rows.get("briefing_evidence_links", []):
            if row["briefing_id"] in self.keep["briefing_cards"]:
                self.keep["briefing_evidence_links"].add(row["id"])
        for row in self.rows.get("debate_rounds", []):
            if row["debate_id"] in self.keep["debate_records"]:
                self.keep["debate_rounds"].add(row["id"])
        for row in self.rows.get("final_adjudications", []):
            if row["debate_id"] in self.keep["debate_records"]:
                self.keep["final_adjudications"].add(row["id"])
        retained_override_targets = set().union(
            self.keep["claim_cards"],
            self.keep["briefing_cards"],
            self.keep["decision_snapshots"],
            self.keep["event_cards"],
            self.keep["final_adjudications"],
            self.keep["speaker_profiles"],
        )
        for row in self.rows.get("user_overrides", []):
            if row["target_id"] in retained_override_targets:
                self.keep["user_overrides"].add(row["id"])
        for row in self.rows.get("vec_claims_map", []):
            if row["claim_id"] in self.keep["claim_cards"]:
                self.keep["vec_claims_map"].add(row["vec_rowid"])

    def compute(self) -> dict[str, set[Any]]:
        self._initialise()
        for _ in range(12):
            before = sum(len(values) for values in self.keep.values())
            self._expand_once()
            if sum(len(values) for values in self.keep.values()) == before:
                break
        else:
            raise RuntimeError("Retention closure did not converge within 12 passes")
        self._add_dependents()
        return self.keep


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        if table in VECTOR_INTERNAL_TABLES:
            continue
        counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return counts


def _prune_id_table(connection: sqlite3.Connection, table: str, keep_ids: Iterable[Any]) -> None:
    values = list(keep_ids)
    if not values:
        connection.execute(f'DELETE FROM "{table}"')
        return
    temp_table = f"_keep_{table}"
    connection.execute(f'CREATE TEMP TABLE "{temp_table}" (id PRIMARY KEY)')
    connection.executemany(
        f'INSERT INTO "{temp_table}"(id) VALUES (?)',
        [(value,) for value in values],
    )
    connection.execute(
        f'DELETE FROM "{table}" WHERE id NOT IN (SELECT id FROM "{temp_table}")'
    )
    connection.execute(f'DROP TABLE "{temp_table}"')


def _load_sqlite_vec(connection: sqlite3.Connection) -> None:
    try:
        import sqlite_vec  # type: ignore
    except ImportError as exc:
        raise RuntimeError("sqlite-vec is required to prune the retained vector index") from exc
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.enable_load_extension(False)


def prune_candidate(connection: sqlite3.Connection, keep: dict[str, set[Any]]) -> None:
    connection.execute("PRAGMA foreign_keys=OFF")
    _load_sqlite_vec(connection)
    connection.execute("BEGIN IMMEDIATE")
    try:
        retained_claim_ids = keep.get("claim_cards", set())
        stale_vectors = connection.execute(
            "SELECT vec_rowid FROM vec_claims_map"
        ).fetchall() if not retained_claim_ids else [
            row
            for row in connection.execute("SELECT vec_rowid, claim_id FROM vec_claims_map")
            if row[1] not in retained_claim_ids
        ]
        for row in stale_vectors:
            connection.execute("DELETE FROM vec_claims WHERE rowid = ?", (row[0],))
        connection.executemany(
            "DELETE FROM vec_claims_map WHERE vec_rowid = ?",
            [(row[0],) for row in stale_vectors],
        )

        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in sorted(ENTITY_TABLES):
            if table in existing_tables:
                _prune_id_table(connection, table, keep.get(table, set()))

        for table in sorted(AUXILIARY_TABLES_TO_CLEAR):
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                connection.execute(f'DELETE FROM "{table}"')

        connection.execute(
            "DELETE FROM claim_raw_source_links "
            "WHERE claim_id NOT IN (SELECT id FROM claim_cards) "
            "OR source_id NOT IN (SELECT id FROM raw_sources)"
        )
        connection.execute(
            "DELETE FROM claim_evidence_links "
            "WHERE claim_id NOT IN (SELECT id FROM claim_cards) "
            "OR evidence_span_id NOT IN (SELECT id FROM evidence_spans)"
        )
        connection.execute(
            "DELETE FROM strategy_source_links "
            "WHERE strategy_id NOT IN (SELECT id FROM strategy_cards) "
            "OR source_id NOT IN (SELECT id FROM raw_sources)"
        )
        connection.execute(
            "DELETE FROM decision_trace_source_links "
            "WHERE trace_event_id NOT IN (SELECT id FROM decision_trace_events) "
            "OR source_id NOT IN (SELECT id FROM raw_sources)"
        )
        connection.execute(
            "UPDATE setup_cards SET strategy_id=NULL "
            "WHERE strategy_id IS NOT NULL "
            "AND strategy_id NOT IN (SELECT id FROM strategy_cards)"
        )
        connection.execute("DROP INDEX IF EXISTS ix_strategy_cards_name_unique")
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("VACUUM")


def citation_quality(connection: sqlite3.Connection) -> dict[str, int]:
    claim_ids = {row[0] for row in connection.execute("SELECT id FROM claim_cards")}
    source_ids = {row[0] for row in connection.execute("SELECT id FROM raw_sources")}
    evidence_ids = {row[0] for row in connection.execute("SELECT id FROM evidence_spans")}
    counts = {
        "citations": 0,
        "missing_claim_ids": 0,
        "missing_source_ids": 0,
        "missing_evidence_span_ids": 0,
    }
    for (payload_value,) in connection.execute(
        "SELECT payload FROM chat_messages WHERE role='assistant'"
    ):
        payload = load_json(payload_value, {})
        for citation in payload.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            counts["citations"] += 1
            claim_id = citation.get("claim_id")
            source_id = citation.get("source_id")
            evidence_id = citation.get("evidence_span_id")
            counts["missing_claim_ids"] += int(bool(claim_id) and claim_id not in claim_ids)
            counts["missing_source_ids"] += int(bool(source_id) and source_id not in source_ids)
            counts["missing_evidence_span_ids"] += int(
                bool(evidence_id) and evidence_id not in evidence_ids
            )
    return counts


def build_candidate(
    source: Path,
    output: Path,
    manifest_path: Path | None = None,
    backup_database: Path | None = None,
    backup_uploads: Path | None = None,
) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source database not found: {source}")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing candidate: {output}")
    if manifest_path is not None and manifest_path.resolve().exists():
        raise FileExistsError(f"Refusing to overwrite existing manifest: {manifest_path}")

    output.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = output.with_name(f"{output.name}.staging-{timestamp}")
    if staging.exists():
        raise FileExistsError(f"Refusing to overwrite existing staging database: {staging}")

    source_uri = f"file:{source.as_posix()}?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True)
    staging_connection = sqlite3.connect(staging)
    try:
        source_connection.backup(staging_connection)
    finally:
        source_connection.close()
        staging_connection.close()

    connection = sqlite3.connect(staging)
    connection.row_factory = sqlite3.Row
    try:
        before_counts = table_counts(connection)
        selection = Selection(connection)
        keep = selection.compute()
        direct_user_source_ids = {
            row["id"]
            for row in selection.rows.get("raw_sources", [])
            if not is_system_source_record(
                source_id=row["id"],
                title=row["title"],
                storage_path=row["storage_path"],
            )
        }
        prune_candidate(connection, keep)
        after_counts = table_counts(connection)
        integrity_check = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_key_violations = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        citations = citation_quality(connection)
        trace_links_to_missing_sources = int(
            connection.execute(
                "SELECT COUNT(*) FROM decision_trace_source_links AS link "
                "LEFT JOIN raw_sources AS source ON source.id = link.source_id "
                "WHERE source.id IS NULL"
            ).fetchone()[0]
        )
        remaining_system_sessions = sum(
            is_system_chat_title(row[0])
            for row in connection.execute("SELECT title FROM chat_sessions")
        )
        remaining_system_sources = sum(
            is_system_source_record(source_id=row[0], title=row[1], storage_path=row[2])
            for row in connection.execute("SELECT id,title,storage_path FROM raw_sources")
        )
    finally:
        connection.close()

    if integrity_check != ["ok"]:
        raise RuntimeError(f"Candidate integrity check failed: {integrity_check}")
    if foreign_key_violations:
        raise RuntimeError(f"Candidate foreign key check failed: {foreign_key_violations}")
    if remaining_system_sessions:
        raise RuntimeError(f"Candidate retains {remaining_system_sessions} system chat sessions")
    if any(citations[key] for key in citations if key.startswith("missing_")):
        raise RuntimeError(f"Candidate contains unresolved citations: {citations}")
    if trace_links_to_missing_sources:
        raise RuntimeError(
            f"Candidate contains {trace_links_to_missing_sources} trace links to missing sources"
        )
    if after_counts.get("key_groups", 0):
        raise RuntimeError("Candidate still contains database-stored API keys")

    staging.replace(output)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": git_head(),
        "selection_policy": "local-v0.1-provenance-closure-v1",
        "source_database": path_record(source),
        "candidate_database": path_record(output),
        "preserved_backups": [
            path_record(path)
            for path in (backup_database, backup_uploads)
            if path is not None
        ],
        "counts_before": before_counts,
        "counts_after": after_counts,
        "counts_removed_from_candidate_only": {
            table: before_counts.get(table, 0) - after_counts.get(table, 0)
            for table in sorted(before_counts)
        },
        "retention": {
            "direct_user_sources": len(direct_user_source_ids),
            "hidden_support_sources": remaining_system_sources,
            "user_sessions": after_counts.get("chat_sessions", 0),
        },
        "quality_checks": {
            "integrity_check": integrity_check,
            "foreign_key_violation_count": len(foreign_key_violations),
            "foreign_key_violations": foreign_key_violations,
            "system_chat_session_count": remaining_system_sessions,
            "stored_citations": citations,
            "decision_trace_links_to_missing_sources": trace_links_to_missing_sources,
            "plaintext_key_group_rows": after_counts.get("key_groups", 0),
        },
    }

    if manifest_path is not None:
        manifest_path = manifest_path.resolve()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return manifest


def main() -> int:
    args = parse_args()
    manifest = build_candidate(
        source=args.source,
        output=args.output,
        manifest_path=args.manifest,
        backup_database=args.backup_database,
        backup_uploads=args.backup_uploads,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
