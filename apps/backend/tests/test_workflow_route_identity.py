from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.workflow_route_identity import (
    BRIEFING_ROUTE_SCOPE,
    COMMITTEE_ROUTE_SCOPE,
    briefing_route_identity,
    committee_route_identity,
)


def _config(**overrides):
    values = {
        "FUSION_MODEL": "fusion/primary",
        "FUSION_FALLBACK_MODEL": "fusion/fallback",
        "FUSION_TEMPERATURE": 0.15,
        "FUSION_MAX_TOKENS": 1800,
        "BRIEFING_MODEL": "briefing/primary",
        "BRIEFING_FALLBACK_MODEL": "briefing/fallback",
        "BRIEFING_TEMPERATURE": 0.2,
        "BRIEFING_MAX_TOKENS": 2500,
        "BRIEFING_GENERAL_MAX_TOKENS": 1400,
        "BRIEFING_VERBOSITY": "max",
        "BRIEFING_REASONING_ENABLED": True,
        "STRATEGY_MODEL": "strategy/primary",
        "STRATEGY_FALLBACK_MODEL": "strategy/fallback",
        "STRATEGY_TEMPERATURE": 0.1,
        "STRATEGY_MAX_TOKENS": 1200,
        "COMMITTEE_MODEL_A": "seat-a/primary",
        "COMMITTEE_MODEL_A_FALLBACK": "seat-a/fallback",
        "COMMITTEE_MODEL_A_TEMPERATURE": 0.2,
        "COMMITTEE_MODEL_A_MAX_TOKENS": 1800,
        "COMMITTEE_MODEL_A_REASONING_EFFORT": "high",
        "COMMITTEE_MODEL_B": "seat-b/primary",
        "COMMITTEE_MODEL_B_FALLBACK": "seat-b/fallback",
        "COMMITTEE_MODEL_B_TEMPERATURE": 0.2,
        "COMMITTEE_MODEL_B_MAX_TOKENS": 1400,
        "COMMITTEE_MODEL_B_REASONING_EFFORT": "medium",
        "COMMITTEE_MODEL_C": "seat-c/primary",
        "COMMITTEE_MODEL_C_FALLBACK": "seat-c/fallback",
        "COMMITTEE_MODEL_C_TEMPERATURE": 0.1,
        "COMMITTEE_MODEL_C_MAX_TOKENS": 1000,
        "COMMITTEE_MODEL_D_OPTIONAL": "seat-d/primary",
        "COMMITTEE_MODEL_D_TEMPERATURE": 0.05,
        "COMMITTEE_MODEL_D_MAX_TOKENS": 900,
        "COMMITTEE_MODEL_E": "seat-e/primary",
        "COMMITTEE_MODEL_E_FALLBACK": "seat-e/fallback",
        "COMMITTEE_MODEL_E_TEMPERATURE": 0.1,
        "COMMITTEE_MODEL_E_MAX_TOKENS": 1000,
        "COMMITTEE_MODEL_F": "seat-f/primary",
        "COMMITTEE_MODEL_F_FALLBACK": "seat-f/fallback",
        "COMMITTEE_MODEL_F_TEMPERATURE": 0.15,
        "COMMITTEE_MODEL_F_MAX_TOKENS": 1000,
        "COMMITTEE_POSTPROCESS_MODEL": "adjudication/primary",
        "COMMITTEE_POSTPROCESS_FALLBACK": "adjudication/fallback",
        "COMMITTEE_POSTPROCESS_TEMPERATURE": 0.2,
        "COMMITTEE_POSTPROCESS_MAX_TOKENS": 2000,
        "REGION_FALLBACK_MODEL": "region/emergency",
        "SELECTED_SCOPE_DIRECT_CLAIM_LIMIT": 24,
        "OPENROUTER_ENABLE_MODEL_FALLBACKS": True,
        "OPENROUTER_SERVICE_TIER": "auto",
        "OPENROUTER_PROVIDER_SORT": "throughput",
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "OPENROUTER_MOCK": False,
        "OPENROUTER_TIMEOUT_SECONDS": 30,
        "OPENROUTER_MAX_RETRIES": 4,
        "OPENROUTER_USE_JSON_SCHEMA": True,
        "OPENROUTER_JSON_SCHEMA_STRICT": True,
        "ENABLE_PROMPT_CACHING": True,
        "OPENROUTER_API_KEY": "must-not-appear",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_briefing_route_identity_is_deterministic_and_secret_free() -> None:
    first = briefing_route_identity(_config())
    second = briefing_route_identity(_config())
    rendered = json.dumps(first, ensure_ascii=False)

    assert first == second
    assert first["scope"] == BRIEFING_ROUTE_SCOPE
    assert first["fingerprint"].startswith("sha256:")
    assert len(first["fingerprint"]) == 71
    assert first["configuration"]["stages"]["briefing"]["primary_model"] == "briefing/primary"
    assert first["configuration"]["stages"]["briefing"]["native_fallback_models"] == [
        "briefing/fallback",
        "region/emergency",
    ]
    assert "must-not-appear" not in rendered
    assert "api_key" not in rendered.lower()


def test_workflow_identity_implementations_are_isolated_by_module() -> None:
    assert briefing_route_identity.__module__ == "app.services.briefing_route_identity"
    assert committee_route_identity.__module__ == "app.services.committee_route_identity"


def test_briefing_route_identity_changes_with_quality_relevant_configuration() -> None:
    baseline = briefing_route_identity(_config())

    assert briefing_route_identity(_config(BRIEFING_MODEL="briefing/candidate"))["fingerprint"] != baseline["fingerprint"]
    assert briefing_route_identity(_config(BRIEFING_GENERAL_MAX_TOKENS=900))["fingerprint"] != baseline["fingerprint"]
    assert briefing_route_identity(_config(FUSION_TEMPERATURE=0.05))["fingerprint"] != baseline["fingerprint"]


def test_committee_route_identity_covers_every_seat_and_adjudication() -> None:
    identity = committee_route_identity(_config())
    routes = identity["configuration"]["routes"]

    assert identity["scope"] == COMMITTEE_ROUTE_SCOPE
    assert set(routes) == {"baseline", "probability", "risk", "structure", "audit", "pnl", "adjudication"}
    assert routes["baseline"]["primary_model"] == "seat-a/primary"
    assert routes["audit"]["native_fallback_models"] == ["seat-e/fallback", "region/emergency"]
    assert routes["adjudication"]["primary_model"] == "adjudication/primary"
    assert routes["adjudication"]["max_tokens"] == 2000


def test_committee_route_identity_changes_when_a_seat_or_fallback_policy_changes() -> None:
    baseline = committee_route_identity(_config())

    assert committee_route_identity(_config(COMMITTEE_MODEL_E="seat-e/candidate"))["fingerprint"] != baseline["fingerprint"]
    assert committee_route_identity(_config(COMMITTEE_POSTPROCESS_MAX_TOKENS=1200))["fingerprint"] != baseline["fingerprint"]
    disabled = committee_route_identity(_config(OPENROUTER_ENABLE_MODEL_FALLBACKS=False))
    assert disabled["fingerprint"] != baseline["fingerprint"]
    assert disabled["configuration"]["routes"]["baseline"]["native_fallback_models"] == []


def test_committee_route_identity_binds_per_run_model_overrides() -> None:
    baseline = committee_route_identity(_config())
    overridden = committee_route_identity(
        _config(),
        model_overrides={"baseline": "seat-a/one-off-candidate"},
    )

    assert overridden["fingerprint"] != baseline["fingerprint"]
    assert overridden["configuration"]["routes"]["baseline"]["primary_model"] == "seat-a/one-off-candidate"
    assert overridden["configuration"]["routes"]["baseline"]["native_fallback_models"] == [
        "seat-a/fallback",
        "region/emergency",
    ]


def test_workflow_identities_preserve_zero_empty_and_transport_state() -> None:
    baseline_briefing = briefing_route_identity(_config())
    baseline_committee = committee_route_identity(_config())
    zero_limit = briefing_route_identity(_config(SELECTED_SCOPE_DIRECT_CLAIM_LIMIT=0))
    empty_model = briefing_route_identity(_config(BRIEFING_MODEL=""))
    mock_committee = committee_route_identity(_config(OPENROUTER_MOCK=True))
    alternate_endpoint = committee_route_identity(
        _config(OPENROUTER_BASE_URL="https://router.internal.example/v1")
    )

    assert zero_limit["configuration"]["policy"]["selected_scope_direct_claim_limit"] == 0
    assert empty_model["configuration"]["stages"]["briefing"]["primary_model"] == ""
    assert zero_limit["fingerprint"] != baseline_briefing["fingerprint"]
    assert empty_model["fingerprint"] != baseline_briefing["fingerprint"]
    assert mock_committee["fingerprint"] != baseline_committee["fingerprint"]
    assert alternate_endpoint["fingerprint"] != baseline_committee["fingerprint"]
    assert "router.internal.example" not in json.dumps(alternate_endpoint)
