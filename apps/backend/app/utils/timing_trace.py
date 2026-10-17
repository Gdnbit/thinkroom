"""Stable wall-clock timing summaries for user-facing workflows."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


def _non_negative_ms(value: Any) -> int | None:
    """Return a finite, non-negative integer duration or ``None`` when invalid."""
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(duration):
        return None
    return max(0, int(round(duration)))


def build_timing_trace(
    *,
    total_ms: Any,
    llm_calls: Iterable[Mapping[str, Any] | None] | None = None,
    phase_timings: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Build a non-overlapping timing trace with explicit residual accounting.

    Callers should pass only non-model phase durations. Model time is derived from
    the individual LLM call traces so retries and fallbacks remain observable.
    """
    total = _non_negative_ms(total_ms) or 0
    result: dict[str, int] = {}
    phase_total = 0

    for key, value in (phase_timings or {}).items():
        if not isinstance(key, str) or not key.endswith("_ms"):
            continue
        duration = _non_negative_ms(value)
        if duration is None:
            continue
        result[key] = duration
        phase_total += duration

    model_total = 0
    for call in llm_calls or ():
        if not isinstance(call, Mapping):
            continue
        duration = _non_negative_ms(call.get("duration_ms"))
        if duration is not None:
            model_total += duration

    result["model_calls_ms"] = model_total
    result["other_processing_ms"] = max(0, total - phase_total - model_total)
    result["total_ms"] = total
    return result
