"""Helpers for binding background jobs to their source objects."""
from __future__ import annotations

from typing import Any


def _clean_ids(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def source_id_from_mapping(value: Any) -> str | None:
    """Read a normalized source id from a JSON-like mapping."""
    if not isinstance(value, dict):
        return None
    raw = value.get("source_id") or value.get("sourceId")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def source_ids_from_mapping(value: Any) -> list[str]:
    """Read normalized source ids from a JSON-like mapping."""
    if not isinstance(value, dict):
        return []
    source_ids = _clean_ids(value.get("source_ids") or value.get("sourceIds"))
    source_id = source_id_from_mapping(value)
    if source_id:
        source_ids = _clean_ids([source_id, *source_ids])
    scope = value.get("scope") or value.get("object_scope") or value.get("objectScope")
    if isinstance(scope, dict):
        source_ids = _clean_ids([*source_ids, *source_ids_from_mapping(scope)])
    return source_ids


def job_source_id(job: Any) -> str | None:
    """Resolve the RawSource id associated with a persisted JobRun."""
    source_ids = job_source_ids(job)
    return source_ids[0] if source_ids else None


def job_source_ids(job: Any) -> list[str]:
    """Resolve RawSource ids associated with a persisted JobRun."""
    if job is None:
        return []

    explicit_source_ids = _clean_ids(getattr(job, "source_ids", None))
    if explicit_source_ids:
        return explicit_source_ids

    related_type = getattr(job, "related_object_type", None)
    related_id = getattr(job, "related_object_id", None)
    if related_type == "raw_source" and isinstance(related_id, str) and related_id.strip():
        return [related_id.strip()]

    source_ids: list[str] = []
    for field_name in ("input_params", "result_summary"):
        source_ids = _clean_ids([*source_ids, *source_ids_from_mapping(getattr(job, field_name, None))])

    summary = getattr(job, "result_summary", None)
    if isinstance(summary, dict):
        source_ids = _clean_ids([*source_ids, *source_ids_from_mapping(summary.get("progress_snapshot"))])

    return source_ids
