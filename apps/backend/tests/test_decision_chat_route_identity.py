from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.model_route_identity import ROUTE_SCOPE, decision_chat_route_identity


def _config(**overrides):
    values = {
        "FUSION_MODEL": "model/primary",
        "FUSION_FALLBACK_MODEL": "model/fallback",
        "REGION_FALLBACK_MODEL": "model/emergency",
        "FUSION_TEMPERATURE": 0.15,
        "FUSION_MAX_TOKENS": 1800,
        "SELECTED_SCOPE_DIRECT_CLAIM_LIMIT": 24,
        "OPENROUTER_ENABLE_MODEL_FALLBACKS": True,
        "OPENROUTER_SERVICE_TIER": "auto",
        "OPENROUTER_PROVIDER_SORT": "throughput",
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "OPENROUTER_MOCK": False,
        "ENABLE_PROMPT_CACHING": True,
        "OPENROUTER_API_KEY": "must-not-appear",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_route_identity_is_deterministic_and_contains_no_credentials() -> None:
    first = decision_chat_route_identity(_config())
    second = decision_chat_route_identity(_config())
    rendered = json.dumps(first, ensure_ascii=False)

    assert first == second
    assert first["scope"] == ROUTE_SCOPE
    assert first["fingerprint"].startswith("sha256:")
    assert len(first["fingerprint"]) == 71
    assert first["configuration"]["native_fallback_models"] == ["model/fallback", "model/emergency"]
    assert "must-not-appear" not in rendered
    assert "api_key" not in rendered.lower()


def test_route_identity_changes_when_a_quality_relevant_setting_changes() -> None:
    baseline = decision_chat_route_identity(_config())
    model_change = decision_chat_route_identity(_config(FUSION_MODEL="model/candidate"))
    temperature_change = decision_chat_route_identity(_config(FUSION_TEMPERATURE=0.05))
    scope_change = decision_chat_route_identity(_config(SELECTED_SCOPE_DIRECT_CLAIM_LIMIT=12))

    assert model_change["fingerprint"] != baseline["fingerprint"]
    assert temperature_change["fingerprint"] != baseline["fingerprint"]
    assert scope_change["fingerprint"] != baseline["fingerprint"]


def test_provider_fallback_policy_is_part_of_the_identity() -> None:
    enabled = decision_chat_route_identity(_config())
    disabled = decision_chat_route_identity(_config(OPENROUTER_ENABLE_MODEL_FALLBACKS=False))

    assert enabled["fingerprint"] != disabled["fingerprint"]
    assert disabled["configuration"]["native_fallback_models"] == []


def test_route_identity_preserves_zero_empty_and_runtime_transport_state() -> None:
    baseline = decision_chat_route_identity(_config())
    zero_limit = decision_chat_route_identity(_config(SELECTED_SCOPE_DIRECT_CLAIM_LIMIT=0))
    empty_model = decision_chat_route_identity(_config(FUSION_MODEL=""))
    mock_mode = decision_chat_route_identity(_config(OPENROUTER_MOCK=True))
    alternate_endpoint = decision_chat_route_identity(
        _config(OPENROUTER_BASE_URL="https://router.internal.example/v1")
    )

    assert zero_limit["configuration"]["selected_scope_direct_claim_limit"] == 0
    assert empty_model["configuration"]["primary_model"] == ""
    assert zero_limit["fingerprint"] != baseline["fingerprint"]
    assert empty_model["fingerprint"] != baseline["fingerprint"]
    assert mock_mode["fingerprint"] != baseline["fingerprint"]
    assert alternate_endpoint["fingerprint"] != baseline["fingerprint"]
    assert "router.internal.example" not in json.dumps(alternate_endpoint)
