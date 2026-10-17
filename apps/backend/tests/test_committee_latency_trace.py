from __future__ import annotations

from app.workflows.committee_workflow import CommitteeWorkflow


def test_committee_timing_trace_accounts_for_model_and_non_model_time():
    trace = CommitteeWorkflow._build_timing_trace(
        total_ms=1_000,
        llm_calls=[{"duration_ms": 200}, {"duration_ms": 300}],
        phase_timings={
            "selected_scope_check_ms": 50,
            "base_context_ms": 100,
            "role_retrieval_ms": 150,
        },
    )

    assert trace == {
        "selected_scope_check_ms": 50,
        "base_context_ms": 100,
        "role_retrieval_ms": 150,
        "model_calls_ms": 500,
        "other_processing_ms": 200,
        "total_ms": 1_000,
    }


def test_committee_timing_trace_never_reports_negative_other_time():
    trace = CommitteeWorkflow._build_timing_trace(
        total_ms=100,
        llm_calls=[{"duration_ms": 120}],
        phase_timings={"base_context_ms": 20},
    )

    assert trace["other_processing_ms"] == 0
    assert trace["total_ms"] == 100
