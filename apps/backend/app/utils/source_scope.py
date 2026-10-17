"""Utilities for normalizing user-selected source scope."""

from collections.abc import Iterable
from typing import Any


def clean_source_ids(source_ids: Any) -> list[str]:
    """Return stable, de-duplicated source ids from request or payload data."""
    if source_ids is None:
        return []

    raw_items: Iterable[Any]
    if isinstance(source_ids, str):
        raw_items = [source_ids]
    elif isinstance(source_ids, Iterable):
        raw_items = source_ids
    else:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for sid in raw_items:
        if not isinstance(sid, str):
            continue
        value = sid.strip()
        if not value or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
    return cleaned
