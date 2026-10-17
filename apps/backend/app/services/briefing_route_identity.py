"""Non-secret route identity for the selected-source briefing workflow."""
from __future__ import annotations

from typing import Any

from app.config import Settings, settings
from app.services.route_identity_common import build_identity, common_policy, model_route


BRIEFING_ROUTE_SCOPE = "briefing_selected_source"
BRIEFING_CONTRACT_VERSION = "briefing-grounded-v1"

_IMPLEMENTATION_FILES = (
    "api/v1/briefings.py",
    "services/briefing_route_identity.py",
    "services/chat_harness.py",
    "services/decision_trace_service.py",
    "services/route_identity_common.py",
    "services/semantic_search.py",
    "services/selected_scope_sufficiency.py",
    "skills/briefing_skill.py",
    "skills/fusion_skill.py",
    "skills/strategy_matching_skill.py",
    "utils/cost_scope.py",
    "utils/chat_quality_report.py",
    "utils/claim_post_processor.py",
    "utils/decision_guardrail.py",
    "utils/json_repair.py",
    "utils/llm_client.py",
    "utils/llm_retry.py",
    "utils/prompt_adapter.py",
    "utils/public_llm_trace.py",
    "utils/role_registry.py",
    "utils/source_scope.py",
    "utils/structured_quality_report.py",
    "mcps/research_memory_mcp.py",
)


def briefing_route_identity(cfg: Settings = settings) -> dict[str, Any]:
    briefing_route = model_route(
        cfg,
        primary_field="BRIEFING_MODEL",
        fallback_field="BRIEFING_FALLBACK_MODEL",
        primary_default="anthropic/claude-sonnet-4.6",
        fallback_default="google/gemini-3.1-pro-preview",
        temperature_field="BRIEFING_TEMPERATURE",
        temperature_default=0.2,
        max_tokens_field="BRIEFING_MAX_TOKENS",
        max_tokens_default=2500,
    )
    briefing_route.update(
        {
            "general_max_tokens": max(1, int(getattr(cfg, "BRIEFING_GENERAL_MAX_TOKENS", 1400) or 1400)),
            "verbosity": str(getattr(cfg, "BRIEFING_VERBOSITY", "max") or "max"),
            "reasoning_enabled": bool(getattr(cfg, "BRIEFING_REASONING_ENABLED", True)),
        }
    )
    configuration = {
        "policy": common_policy(cfg),
        "stages": {
            "fusion": model_route(
                cfg,
                primary_field="FUSION_MODEL",
                fallback_field="FUSION_FALLBACK_MODEL",
                primary_default="z-ai/glm-5",
                fallback_default="google/gemini-3-flash-preview",
                temperature_field="FUSION_TEMPERATURE",
                temperature_default=0.15,
                max_tokens_field="FUSION_MAX_TOKENS",
                max_tokens_default=1800,
            ),
            "briefing": briefing_route,
            "strategy_matching": model_route(
                cfg,
                primary_field="STRATEGY_MODEL",
                fallback_field="STRATEGY_FALLBACK_MODEL",
                primary_default="anthropic/claude-opus-4.7",
                fallback_default="anthropic/claude-sonnet-4.6",
                temperature_field="STRATEGY_TEMPERATURE",
                temperature_default=0.1,
                max_tokens_field="STRATEGY_MAX_TOKENS",
                max_tokens_default=1200,
            ),
        },
    }
    return build_identity(
        scope=BRIEFING_ROUTE_SCOPE,
        contract_version=BRIEFING_CONTRACT_VERSION,
        configuration=configuration,
        implementation_files=_IMPLEMENTATION_FILES,
    )


__all__ = ["BRIEFING_CONTRACT_VERSION", "BRIEFING_ROUTE_SCOPE", "briefing_route_identity"]
