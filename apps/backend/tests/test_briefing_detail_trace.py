from datetime import datetime, timezone

from ulid import ULID


def test_get_briefing_returns_guardrail_and_trace_from_job_summary(client, db):
    from app.schemas.briefing_card import BriefingCard
    from app.schemas.job_run import JobRun

    now = datetime.now(timezone.utc)
    briefing_id = str(ULID())
    db.add(BriefingCard(
        id=briefing_id,
        title="Scoped briefing",
        topic="scope",
        question="test",
        direction="neutral",
        setup_quality="low",
        asset_scope=["GENERAL"],
        executive_summary="summary",
        supporting_points=[],
        contrary_points=[],
        key_risks=[],
        strategy_fit_summary=None,
        recommended_action="watch",
        invalidation_conditions=[],
        source_claim_ids=[],
        source_event_ids=[],
        model_used="model-x",
        committee_level="single",
        status="active",
        generated_at=now,
        created_at=now,
        updated_at=now,
    ))
    private_provider_error = "historical briefing provider detail must not leak"
    db.add(JobRun(
        id=str(ULID()),
        job_type="briefing_gen",
        status="completed",
        triggered_by="test",
        input_params={},
        result_summary={
            "decision_guardrail": {"status": "passed", "effective_action": "watch"},
            "llm_trace": {
                "call_count": 2,
                "total_cost_usd": 0.01,
                "error": private_provider_error,
                "error_type": "timeout",
                "calls": [
                    {
                        "model": "model-a",
                        "cost_usd": 0.004,
                        "error": private_provider_error,
                        "error_type": "timeout",
                    },
                    {"model": "model-b", "cost_usd": 0.006},
                ],
            },
            "timing_trace": {
                "retrieval_ms": 200,
                "model_calls_ms": 700,
                "other_processing_ms": 334,
                "total_ms": 1234,
            },
            "quality_report": {
                "decision_ready": False,
                "severity": "warning",
                "warnings": [{"code": "insufficient_evidence", "message": "选定资料不足"}],
                "evidence": {"claims_used": 0, "events_used": 0, "has_market": False},
            },
            "quality_filters": {
                "evidence_sufficiency": "insufficient",
                "insufficient_evidence_reason": "selected_sources_claim_validity_window_mismatch",
            },
            "claims_used": 0,
            "source_claim_ids": [],
            "scope": {"mode": "selected_sources", "source_ids": ["src-1"], "asset": "GENERAL"},
        },
        related_object_id=briefing_id,
        related_object_type="briefing",
        started_at=now,
        completed_at=now,
        duration_ms=1234,
        retry_count=0,
        created_at=now,
        updated_at=now,
    ))
    db.commit()

    resp = client.get(f"/api/v1/briefings/{briefing_id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["decision_guardrail"]["status"] == "passed"
    assert data["llm_trace"]["call_count"] == 2
    assert data["llm_trace"]["error_type"] == "timeout"
    assert "error" not in data["llm_trace"]
    assert all("error" not in call for call in data["llm_trace"]["calls"])
    assert private_provider_error not in resp.text
    assert data["timing_trace"]["total_ms"] == 1234
    assert data["timing_trace"]["retrieval_ms"] == 200
    assert data["quality_report"]["decision_ready"] is False
    assert data["quality_report"]["warnings"][0]["code"] == "insufficient_evidence"
    assert data["duration_ms"] == 1234
    assert data["quality_filters"]["evidence_sufficiency"] == "insufficient"
    assert (
        data["quality_filters"]["insufficient_evidence_reason"]
        == "selected_sources_claim_validity_window_mismatch"
    )
    assert data["claims_used"] == 0
    assert data["source_claim_ids"] == []
    assert data["scope"]["mode"] == "selected_sources"
