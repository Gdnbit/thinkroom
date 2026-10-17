from __future__ import annotations


def test_build_timing_trace_accounts_for_model_and_non_model_phases():
    from app.utils.timing_trace import build_timing_trace

    trace = build_timing_trace(
        total_ms=1_000,
        llm_calls=[{"duration_ms": 200}, {"duration_ms": 300}],
        phase_timings={"retrieval_ms": 120, "quality_guardrail_ms": 80},
    )

    assert trace == {
        "retrieval_ms": 120,
        "quality_guardrail_ms": 80,
        "model_calls_ms": 500,
        "other_processing_ms": 300,
        "total_ms": 1_000,
    }


def test_build_timing_trace_ignores_invalid_values_and_never_goes_negative():
    from app.utils.timing_trace import build_timing_trace

    trace = build_timing_trace(
        total_ms=100,
        llm_calls=[{"duration_ms": 120}, None, {"duration_ms": "invalid"}],
        phase_timings={"retrieval_ms": -5, "not_a_duration": 40},
    )

    assert trace["retrieval_ms"] == 0
    assert "not_a_duration" not in trace
    assert trace["model_calls_ms"] == 120
    assert trace["other_processing_ms"] == 0
    assert trace["total_ms"] == 100
