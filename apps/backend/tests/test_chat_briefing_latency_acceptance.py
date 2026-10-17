from __future__ import annotations


def _runtime_report(*, chat_ms: int, briefing_ms: int, mock: bool = False) -> dict:
    def mode(total_ms: int, model_ms: int, cost: float, *, chat: bool) -> dict:
        result = {
            "ok": True,
            "status": 200,
            "quality_severity": "ok",
            "timing_trace": {
                "retrieval_ms": 10,
                "model_calls_ms": model_ms,
                "other_processing_ms": total_ms - model_ms - 10,
                "total_ms": total_ms,
            },
            "llm_trace": {
                "call_count": 1,
                "total_cost_usd": cost,
                "calls": [
                    {
                        "model": "test/model",
                        "mock": mock,
                        "duration_ms": model_ms,
                        "cost_usd": cost,
                        "cost_source": "provider_usage",
                        "generation_id": "gen-test",
                    }
                ],
            },
        }
        if chat:
            result["guardrail_status"] = "passed"
        return result

    return {
        "steps": {
            "chat": mode(chat_ms, chat_ms - 30, 0.003, chat=True),
            "briefing": mode(briefing_ms, briefing_ms - 40, 0.03, chat=False),
            "assessment": {"status": "passed", "require_real": True},
        }
    }


def test_latency_acceptance_requires_three_auditable_real_samples():
    from app.verification.chat_briefing_latency_acceptance import assess_reports

    result = assess_reports(
        [
            ("sample-a.json", _runtime_report(chat_ms=8_000, briefing_ms=25_000)),
            ("sample-b.json", _runtime_report(chat_ms=12_000, briefing_ms=30_000)),
            ("sample-c.json", _runtime_report(chat_ms=15_000, briefing_ms=50_000)),
        ]
    )

    assert result["status"] == "passed"
    assert result["metrics"]["chat"]["p50_total_ms"] == 12_000
    assert result["metrics"]["chat"]["p95_total_ms"] == 15_000
    assert result["metrics"]["briefing"]["p50_total_ms"] == 30_000
    assert result["metrics"]["briefing"]["p95_total_ms"] == 50_000


def test_latency_acceptance_rejects_mock_or_too_few_samples():
    from app.verification.chat_briefing_latency_acceptance import assess_reports

    result = assess_reports(
        [
            ("sample-a.json", _runtime_report(chat_ms=8_000, briefing_ms=25_000, mock=True)),
            ("sample-b.json", _runtime_report(chat_ms=12_000, briefing_ms=30_000)),
        ]
    )

    assert result["status"] == "failed"
    assert "sample_count_below_minimum" in result["errors"]
    assert "sample_1.chat.call_1.mock_not_allowed" in result["errors"]
