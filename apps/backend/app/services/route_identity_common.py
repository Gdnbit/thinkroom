"""Shared, non-secret hashing primitives for workflow route identities."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings


ROUTE_IDENTITY_SCHEMA_VERSION = 1
_MISSING = object()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalized_model(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def configured_text(cfg: Settings, field: str, default: str) -> str:
    """Read configured text without disguising an explicit empty runtime value."""
    value = getattr(cfg, field, _MISSING)
    if value is _MISSING or value is None:
        return default
    return str(value).strip()


def unique_models(values: list[str], *, exclude: set[str] | None = None) -> list[str]:
    excluded = exclude or set()
    result: list[str] = []
    for value in values:
        if not value or value in excluded or value in result:
            continue
        result.append(value)
    return result


def common_policy(cfg: Settings) -> dict[str, Any]:
    endpoint = configured_text(cfg, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return {
        "provider_fallbacks_enabled": bool(getattr(cfg, "OPENROUTER_ENABLE_MODEL_FALLBACKS", True)),
        "region_fallback_model": configured_text(cfg, "REGION_FALLBACK_MODEL", "z-ai/glm-5"),
        "service_tier": str(getattr(cfg, "OPENROUTER_SERVICE_TIER", "auto") or "auto"),
        "provider_sort": str(getattr(cfg, "OPENROUTER_PROVIDER_SORT", "throughput") or "throughput"),
        "timeout_seconds": max(1, int(getattr(cfg, "OPENROUTER_TIMEOUT_SECONDS", 30) or 30)),
        "max_retries": max(0, int(getattr(cfg, "OPENROUTER_MAX_RETRIES", 4) or 0)),
        "json_schema_enabled": bool(getattr(cfg, "OPENROUTER_USE_JSON_SCHEMA", True)),
        "json_schema_strict": bool(getattr(cfg, "OPENROUTER_JSON_SCHEMA_STRICT", True)),
        "prompt_caching_enabled": bool(getattr(cfg, "ENABLE_PROMPT_CACHING", True)),
        "mock_enabled": bool(getattr(cfg, "OPENROUTER_MOCK", False)),
        "endpoint_fingerprint": canonical_hash({"endpoint": endpoint.rstrip("/")}),
        "selected_scope_direct_claim_limit": max(
            0,
            int(getattr(cfg, "SELECTED_SCOPE_DIRECT_CLAIM_LIMIT", 24)),
        ),
    }


def model_route(
    cfg: Settings,
    *,
    primary_field: str,
    fallback_field: str | None,
    primary_default: str,
    fallback_default: str = "",
    temperature_field: str | None = None,
    temperature_default: float | None = None,
    max_tokens_field: str | None = None,
    max_tokens_default: int | None = None,
    reasoning_effort_field: str | None = None,
) -> dict[str, Any]:
    primary = configured_text(cfg, primary_field, primary_default)
    fallback = (
        configured_text(cfg, fallback_field, fallback_default)
        if fallback_field
        else ""
    )
    region = configured_text(cfg, "REGION_FALLBACK_MODEL", "z-ai/glm-5")
    fallbacks_enabled = bool(getattr(cfg, "OPENROUTER_ENABLE_MODEL_FALLBACKS", True))
    route: dict[str, Any] = {
        "primary_model": primary,
        "configured_fallback_model": fallback or None,
        "native_fallback_models": (
            unique_models([fallback, region], exclude={primary}) if fallbacks_enabled else []
        ),
    }
    if temperature_field:
        configured_temperature = getattr(cfg, temperature_field, None)
        route["temperature"] = float(
            configured_temperature if configured_temperature is not None else temperature_default
        )
    if max_tokens_field:
        route["max_tokens"] = max(
            1,
            int(getattr(cfg, max_tokens_field, max_tokens_default) or max_tokens_default or 1),
        )
    if reasoning_effort_field:
        route["reasoning_effort"] = str(getattr(cfg, reasoning_effort_field, "") or "") or None
    return route


@lru_cache(maxsize=8)
def implementation_fingerprint(contract_version: str, files: tuple[str, ...]) -> tuple[str, int]:
    app_root = Path(__file__).resolve().parents[1]
    file_digests: dict[str, str] = {}
    for relative_path in files:
        path = app_root / relative_path
        try:
            source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
            file_digests[relative_path] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        except OSError:
            file_digests[relative_path] = "unavailable"
    return canonical_hash({"contract_version": contract_version, "files": file_digests}), len(file_digests)


def build_identity(
    *,
    scope: str,
    contract_version: str,
    configuration: dict[str, Any],
    implementation_files: tuple[str, ...],
) -> dict[str, Any]:
    configuration_fingerprint = canonical_hash(configuration)
    implementation_hash, implementation_file_count = implementation_fingerprint(
        contract_version,
        implementation_files,
    )
    fingerprint = canonical_hash(
        {
            "schema_version": ROUTE_IDENTITY_SCHEMA_VERSION,
            "scope": scope,
            "contract_version": contract_version,
            "configuration_fingerprint": configuration_fingerprint,
            "implementation_fingerprint": implementation_hash,
        }
    )
    return {
        "schema_version": ROUTE_IDENTITY_SCHEMA_VERSION,
        "scope": scope,
        "contract_version": contract_version,
        "fingerprint": fingerprint,
        "configuration_fingerprint": configuration_fingerprint,
        "implementation_fingerprint": implementation_hash,
        "implementation_file_count": implementation_file_count,
        "configuration": configuration,
    }
