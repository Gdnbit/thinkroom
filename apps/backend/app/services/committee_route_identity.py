"""Non-secret route identity for the selected-source committee workflow."""
from __future__ import annotations

from typing import Any

from app.config import Settings, settings
from app.services.route_identity_common import (
    build_identity,
    common_policy,
    model_route,
    unique_models,
)


COMMITTEE_ROUTE_SCOPE = "committee_selected_source"
COMMITTEE_CONTRACT_VERSION = "committee-grounded-v1"

_IMPLEMENTATION_FILES = (
    "api/v1/committee.py",
    "services/agent_retrieval.py",
    "services/committee_orchestrator.py",
    "services/committee_route_identity.py",
    "services/decision_trace_service.py",
    "services/route_identity_common.py",
    "services/semantic_search.py",
    "services/selected_scope_sufficiency.py",
    "skills/committee_debate_skill.py",
    "skills/final_adjudication_skill.py",
    "utils/decision_guardrail.py",
    "utils/chat_quality_report.py",
    "utils/claim_post_processor.py",
    "utils/cost_scope.py",
    "utils/json_repair.py",
    "utils/llm_client.py",
    "utils/llm_retry.py",
    "utils/prompt_adapter.py",
    "utils/public_llm_trace.py",
    "utils/role_registry.py",
    "utils/source_scope.py",
    "utils/structured_quality_report.py",
    "mcps/research_memory_mcp.py",
    "workflows/committee_workflow.py",
)


def committee_route_identity(
    cfg: Settings = settings,
    *,
    model_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    route_specs = {
        "baseline": ("A", "COMMITTEE_MODEL_A", "COMMITTEE_MODEL_A_FALLBACK", "anthropic/claude-opus-4.7", "anthropic/claude-sonnet-4.6"),
        "probability": ("B", "COMMITTEE_MODEL_B", "COMMITTEE_MODEL_B_FALLBACK", "google/gemini-3.1-pro-preview", "google/gemini-3-flash-preview"),
        "risk": ("C", "COMMITTEE_MODEL_C", "COMMITTEE_MODEL_C_FALLBACK", "openai/gpt-5.4", "openai/gpt-5.4-mini"),
        "structure": ("D", "COMMITTEE_MODEL_D_OPTIONAL", None, "z-ai/glm-5", ""),
        "audit": ("E", "COMMITTEE_MODEL_E", "COMMITTEE_MODEL_E_FALLBACK", "google/gemini-3-flash-preview", "z-ai/glm-5"),
        "pnl": ("F", "COMMITTEE_MODEL_F", "COMMITTEE_MODEL_F_FALLBACK", "google/gemini-3-flash-preview", "xiaomi/mimo-v2-flash"),
    }
    routes: dict[str, Any] = {}
    for seat, (letter, primary_field, fallback_field, primary_default, fallback_default) in route_specs.items():
        routes[seat] = model_route(
            cfg,
            primary_field=primary_field,
            fallback_field=fallback_field,
            primary_default=primary_default,
            fallback_default=fallback_default,
            temperature_field=f"COMMITTEE_MODEL_{letter}_TEMPERATURE",
            temperature_default=0.2,
            max_tokens_field=f"COMMITTEE_MODEL_{letter}_MAX_TOKENS",
            max_tokens_default=1200,
            reasoning_effort_field=f"COMMITTEE_MODEL_{letter}_REASONING_EFFORT",
        )
    policy = common_policy(cfg)
    for seat, override in (model_overrides or {}).items():
        normalized = str(override or "").strip()
        if seat not in routes or not normalized:
            continue
        routes[seat]["primary_model"] = normalized
        routes[seat]["native_fallback_models"] = (
            unique_models(
                [
                    str(routes[seat].get("configured_fallback_model") or ""),
                    str(policy["region_fallback_model"]),
                ],
                exclude={normalized},
            )
            if policy["provider_fallbacks_enabled"]
            else []
        )
    routes["adjudication"] = model_route(
        cfg,
        primary_field="COMMITTEE_POSTPROCESS_MODEL",
        fallback_field="COMMITTEE_POSTPROCESS_FALLBACK",
        primary_default="openai/gpt-5.4-mini",
        fallback_default="openai/gpt-5.4-nano",
        temperature_field="COMMITTEE_POSTPROCESS_TEMPERATURE",
        temperature_default=0.2,
        max_tokens_field="COMMITTEE_POSTPROCESS_MAX_TOKENS",
        max_tokens_default=2000,
    )
    configuration = {"policy": policy, "routes": routes}
    return build_identity(
        scope=COMMITTEE_ROUTE_SCOPE,
        contract_version=COMMITTEE_CONTRACT_VERSION,
        configuration=configuration,
        implementation_files=_IMPLEMENTATION_FILES,
    )


__all__ = ["COMMITTEE_CONTRACT_VERSION", "COMMITTEE_ROUTE_SCOPE", "committee_route_identity"]
