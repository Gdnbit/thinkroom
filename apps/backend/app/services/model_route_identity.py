"""Non-secret identity for the decision-chat model route and its core implementation."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings, settings


ROUTE_IDENTITY_SCHEMA_VERSION = 1
ROUTE_SCOPE = "decision_chat_selected_source"
ROUTE_CONTRACT_VERSION = "decision-chat-grounded-v1"
_MISSING = object()

_IMPLEMENTATION_FILES = (
    "api/v1/chat.py",
    "services/chat_harness.py",
    "services/decision_trace_service.py",
    "services/model_route_identity.py",
    "services/semantic_search.py",
    "services/selected_scope_sufficiency.py",
    "utils/chat_answer_normalizer.py",
    "utils/chat_quality_report.py",
    "utils/claim_post_processor.py",
    "utils/citation_stance_checker.py",
    "utils/citation_validator.py",
    "utils/decision_guardrail.py",
    "utils/llm_client.py",
    "utils/prompt_adapter.py",
    "utils/public_llm_trace.py",
    "utils/role_registry.py",
    "utils/source_scope.py",
)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _model(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _configured_text(cfg: Settings, field: str, default: str) -> str:
    value = getattr(cfg, field, _MISSING)
    if value is _MISSING or value is None:
        return default
    return str(value).strip()


def _unique_models(values: list[str], *, exclude: set[str] | None = None) -> list[str]:
    excluded = exclude or set()
    result: list[str] = []
    for value in values:
        if not value or value in excluded or value in result:
            continue
        result.append(value)
    return result


@lru_cache(maxsize=1)
def _implementation_fingerprint() -> tuple[str, int]:
    app_root = Path(__file__).resolve().parents[1]
    file_digests: dict[str, str] = {}
    for relative_path in _IMPLEMENTATION_FILES:
        path = app_root / relative_path
        try:
            source = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
            file_digests[relative_path] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        except OSError:
            file_digests[relative_path] = "unavailable"
    return _canonical_hash({"contract_version": ROUTE_CONTRACT_VERSION, "files": file_digests}), len(file_digests)


def decision_chat_route_identity(cfg: Settings = settings) -> dict[str, Any]:
    """Return an inspectable route identity without reading or exposing credentials."""

    primary_model = _configured_text(cfg, "FUSION_MODEL", "z-ai/glm-5")
    manual_retry_model = _model(
        getattr(cfg, "FUSION_FALLBACK_MODEL", None) or getattr(cfg, "REGION_FALLBACK_MODEL", None),
        "google/gemini-3-flash-preview",
    )
    region_fallback_model = _configured_text(cfg, "REGION_FALLBACK_MODEL", "z-ai/glm-5")
    provider_fallbacks_enabled = bool(getattr(cfg, "OPENROUTER_ENABLE_MODEL_FALLBACKS", True))
    endpoint = _configured_text(cfg, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    native_fallback_models = (
        _unique_models([manual_retry_model, region_fallback_model], exclude={primary_model})
        if provider_fallbacks_enabled
        else []
    )
    max_tokens = max(512, int(getattr(cfg, "FUSION_MAX_TOKENS", 1024) or 1024))
    retry_max_tokens = max(max_tokens, 1536)
    temperature = float(getattr(cfg, "FUSION_TEMPERATURE", 0.2) or 0.2)

    configuration = {
        "primary_model": primary_model,
        "manual_retry_model": manual_retry_model,
        "region_fallback_model": region_fallback_model,
        "native_fallback_models": native_fallback_models,
        "provider_fallbacks_enabled": provider_fallbacks_enabled,
        "service_tier": str(getattr(cfg, "OPENROUTER_SERVICE_TIER", "auto") or "auto"),
        "provider_sort": str(getattr(cfg, "OPENROUTER_PROVIDER_SORT", "throughput") or "throughput"),
        "mock_enabled": bool(getattr(cfg, "OPENROUTER_MOCK", False)),
        "endpoint_fingerprint": _canonical_hash({"endpoint": endpoint.rstrip("/")}),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "retry_max_tokens": retry_max_tokens,
        "selected_scope_direct_claim_limit": max(
            0,
            int(getattr(cfg, "SELECTED_SCOPE_DIRECT_CLAIM_LIMIT", 24)),
        ),
        "prompt_caching_enabled": bool(getattr(cfg, "ENABLE_PROMPT_CACHING", True)),
    }
    configuration_fingerprint = _canonical_hash(configuration)
    implementation_fingerprint, implementation_file_count = _implementation_fingerprint()
    fingerprint = _canonical_hash(
        {
            "schema_version": ROUTE_IDENTITY_SCHEMA_VERSION,
            "scope": ROUTE_SCOPE,
            "contract_version": ROUTE_CONTRACT_VERSION,
            "configuration_fingerprint": configuration_fingerprint,
            "implementation_fingerprint": implementation_fingerprint,
        }
    )
    return {
        "schema_version": ROUTE_IDENTITY_SCHEMA_VERSION,
        "scope": ROUTE_SCOPE,
        "contract_version": ROUTE_CONTRACT_VERSION,
        "fingerprint": fingerprint,
        "configuration_fingerprint": configuration_fingerprint,
        "implementation_fingerprint": implementation_fingerprint,
        "implementation_file_count": implementation_file_count,
        "configuration": configuration,
    }


__all__ = [
    "ROUTE_CONTRACT_VERSION",
    "ROUTE_IDENTITY_SCHEMA_VERSION",
    "ROUTE_SCOPE",
    "decision_chat_route_identity",
]
