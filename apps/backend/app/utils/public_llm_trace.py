"""Safe, stable projection for LLM traces exposed outside the backend."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_PUBLIC_KEYS = frozenset(
    {
        "actual_model",
        "call_count",
        "call_index",
        "calls",
        "completion_tokens",
        "configured_fallback_models",
        "cost_source",
        "cost_usd",
        "duration_ms",
        "error_type",
        "fallback_chain",
        "generation_id",
        "input_tokens",
        "integrity_fallback_applied",
        "integrity_fallback_reasons",
        "integrity_prune_succeeded",
        "integrity_reasons",
        "integrity_retry_attempted",
        "integrity_retry_reasons",
        "integrity_retry_succeeded",
        "mock",
        "model",
        "output_tokens",
        "prompt_hash",
        "prompt_tokens",
        "requested_model",
        "response_chars",
        "response_hash",
        "retry_count",
        "schema_attempt",
        "schema_retry_count",
        "skill_role",
        "step",
        "total_cost_usd",
        "total_input_tokens",
        "total_output_tokens",
        "total_tokens",
    }
)
_STRING_KEYS = frozenset(
    {
        "actual_model",
        "cost_source",
        "generation_id",
        "model",
        "prompt_hash",
        "requested_model",
        "response_hash",
        "skill_role",
        "step",
    }
)
_STRING_LIST_KEYS = frozenset(
    {
        "configured_fallback_models",
        "fallback_chain",
        "integrity_fallback_reasons",
        "integrity_reasons",
        "integrity_retry_reasons",
    }
)
_ERROR_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")
_MAX_CALLS = 128
_MAX_LIST_ITEMS = 64
_MAX_STRING_LENGTH = 240


def _safe_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    return normalized[:_MAX_STRING_LENGTH]


def _safe_error_type(value: Any, *, had_raw_error: bool) -> str | None:
    normalized = _safe_string(value)
    if normalized and _ERROR_TYPE_PATTERN.fullmatch(normalized):
        return normalized
    return "llm_call_failed" if had_raw_error else None


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _safe_string(value)


def public_llm_trace(value: Any) -> dict[str, Any]:
    """Return an allowlisted trace without provider error text or unknown fields.

    Provider exception text is deliberately never part of the public contract. A
    stable ``error_type`` is retained, or synthesized when a raw error was the
    only failure signal.
    """

    if not isinstance(value, Mapping):
        return {}

    projected: dict[str, Any] = {}
    had_raw_error = bool(value.get("error"))

    for key in _PUBLIC_KEYS - {"calls", "error_type"}:
        if key not in value:
            continue
        raw = value.get(key)
        if key in _STRING_KEYS:
            cleaned = _safe_string(raw)
        elif key in _STRING_LIST_KEYS:
            cleaned = [
                item
                for item in (_safe_string(candidate) for candidate in raw[:_MAX_LIST_ITEMS])
                if item is not None
            ] if isinstance(raw, list) else []
        else:
            cleaned = _safe_scalar(raw)
        if cleaned is not None:
            projected[key] = cleaned

    error_type = _safe_error_type(value.get("error_type"), had_raw_error=had_raw_error)
    if error_type is not None:
        projected["error_type"] = error_type

    raw_calls = value.get("calls")
    if isinstance(raw_calls, list):
        calls = [
            public_llm_trace(call)
            for call in raw_calls[:_MAX_CALLS]
            if isinstance(call, Mapping)
        ]
        projected["calls"] = calls
        projected["call_count"] = len(calls)

    return projected


def public_llm_trace_payload(value: Any) -> dict[str, Any]:
    """Copy a response/message payload and project its optional LLM trace."""

    if not isinstance(value, Mapping):
        return {}
    payload = dict(value)
    if "llm_trace" in payload:
        payload["llm_trace"] = public_llm_trace(payload.get("llm_trace"))
    return payload


__all__ = ["public_llm_trace", "public_llm_trace_payload"]
