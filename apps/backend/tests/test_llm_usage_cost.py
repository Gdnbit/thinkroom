from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.config import settings
from app.utils.llm_client import LLMClient


def _response(*, model: str, cost: float | None):
    usage = SimpleNamespace(
        prompt_tokens=1_000,
        completion_tokens=500,
        total_tokens=1_500,
    )
    if cost is not None:
        usage.cost = cost
    return SimpleNamespace(
        id="gen-cost-regression",
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content="A grounded answer."))],
        usage=usage,
    )


def _complete_with_response(monkeypatch, response):
    client = LLMClient()
    monkeypatch.setattr(settings, "OPENROUTER_MOCK", False)
    monkeypatch.setattr(client, "_get_openai_client", lambda _role=None: object())
    monkeypatch.setattr(client, "_call_with_retry", lambda *_args, **_kwargs: response)
    client.complete(
        system_prompt="Use the supplied evidence.",
        user_prompt="Summarize it.",
        model="openai/gpt-5.4-mini",
        skill_role="fusion",
        max_tokens=256,
    )
    return client.get_last_usage()


def test_complete_prefers_openrouter_reported_cost_and_records_generation_id(monkeypatch):
    usage = _complete_with_response(
        monkeypatch,
        _response(model="openai/gpt-5.4-mini-20260317", cost=0.012345),
    )

    assert usage["cost_usd"] == pytest.approx(0.012345)
    assert usage["cost_source"] == "provider_usage"
    assert usage["generation_id"] == "gen-cost-regression"


def test_complete_estimates_versioned_model_cost_when_provider_cost_is_missing(monkeypatch):
    usage = _complete_with_response(
        monkeypatch,
        _response(model="openai/gpt-5.4-mini-20260317", cost=None),
    )

    expected = (1_000 / 1_000_000) * 0.75 + (500 / 1_000_000) * 4.50
    assert usage["cost_usd"] == pytest.approx(expected)
    assert usage["cost_source"] == "static_estimate"


def test_provider_reported_zero_cost_remains_authoritative(monkeypatch):
    usage = _complete_with_response(
        monkeypatch,
        _response(model="openrouter/unknown-cache-hit", cost=0.0),
    )

    assert usage["cost_usd"] == 0.0
    assert usage["cost_source"] == "provider_usage"


def test_usage_and_failure_state_are_isolated_between_concurrent_requests():
    client = LLMClient()
    barrier = Barrier(2)

    def capture(model: str, error_type: str):
        client._last_usage = {"model": model, "cost_usd": 0.01}
        client._last_failure_type = error_type
        barrier.wait(timeout=5)
        return client.get_last_usage(), client._last_failure_type

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(capture, "model-a", "timeout")
        second = executor.submit(capture, "model-b", "rate_limited")
        first_usage, first_error = first.result(timeout=10)
        second_usage, second_error = second.result(timeout=10)

    assert first_usage["model"] == "model-a"
    assert first_error == "timeout"
    assert second_usage["model"] == "model-b"
    assert second_error == "rate_limited"
    assert client.get_last_usage() == {}
    assert client._last_failure_type is None


@pytest.mark.asyncio
async def test_usage_state_is_isolated_between_concurrent_async_tasks():
    client = LLMClient()
    barrier = asyncio.Barrier(2)

    async def capture(model: str):
        client._last_usage = {"model": model}
        await barrier.wait()
        return client.get_last_usage()

    first_usage, second_usage = await asyncio.gather(
        capture("async-model-a"),
        capture("async-model-b"),
    )

    assert first_usage["model"] == "async-model-a"
    assert second_usage["model"] == "async-model-b"
    assert client.get_last_usage() == {}
