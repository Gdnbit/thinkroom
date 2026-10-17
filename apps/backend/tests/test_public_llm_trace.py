from __future__ import annotations

from app.utils.public_llm_trace import public_llm_trace, public_llm_trace_payload


def test_public_llm_trace_removes_provider_error_text_recursively() -> None:
    secret = "provider credential fragment sk-sensitive-must-not-leak"
    source = {
        "call_count": 2,
        "total_cost_usd": 0.03,
        "error": secret,
        "error_type": "rate_limited",
        "unknown_internal_field": secret,
        "calls": [
            {
                "step": "chat_generation",
                "model": "provider/model",
                "duration_ms": 120,
                "cost_usd": 0.01,
                "error": secret,
                "error_type": "rate_limited",
            },
            {
                "step": "fallback_generation",
                "model": "provider/fallback",
                "duration_ms": 180,
                "cost_usd": 0.02,
                "error": secret,
            },
        ],
    }

    result = public_llm_trace(source)

    assert result["call_count"] == 2
    assert result["error_type"] == "rate_limited"
    assert result["calls"][0]["error_type"] == "rate_limited"
    assert result["calls"][1]["error_type"] == "llm_call_failed"
    assert "error" not in result
    assert all("error" not in call for call in result["calls"])
    assert "unknown_internal_field" not in result
    assert secret not in str(result)
    assert source["calls"][0]["error"] == secret


def test_public_llm_trace_rejects_free_form_error_type() -> None:
    secret = "free form error with credential material"

    assert public_llm_trace({"error": secret, "error_type": secret}) == {
        "error_type": "llm_call_failed"
    }


def test_public_llm_trace_payload_projects_historical_trace_without_mutating_input() -> None:
    source = {
        "mode": "chat",
        "llm_trace": {
            "model": "provider/model",
            "error": "historical private provider error",
            "error_type": "timeout",
        },
    }

    result = public_llm_trace_payload(source)

    assert result == {
        "mode": "chat",
        "llm_trace": {"model": "provider/model", "error_type": "timeout"},
    }
    assert "error" in source["llm_trace"]
