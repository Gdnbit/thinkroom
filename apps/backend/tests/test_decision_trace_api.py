from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink
from app.services.decision_trace_service import record_decision_trace_bundle


def _event(
    *,
    event_id: str,
    related_object_type: str,
    related_object_id: str,
    event_type: str,
    created_at: datetime,
    source_ids: list[str] | None = None,
    llm_payload_extra: dict | None = None,
    write_links: bool = True,
) -> DecisionTraceEvent:
    cleaned_source_ids = source_ids or ["source-a"]
    event = DecisionTraceEvent(
        id=event_id,
        related_object_type=related_object_type,
        related_object_id=related_object_id,
        run_id="run-api-test",
        session_id="session-api-test",
        job_id=None,
        event_type=event_type,
        step_name=event_type,
        status="ok",
        model="mock/model" if event_type == "llm_call" else None,
        skill_role="fusion" if event_type == "llm_call" else None,
        duration_ms=120 if event_type == "llm_call" else None,
        cost_usd=0.01 if event_type == "llm_call" else None,
        source_ids=cleaned_source_ids,
        payload=(
            {
                "event": event_type,
                "error": "historical provider detail must not leak",
                "error_type": "rate_limited",
                **(llm_payload_extra or {}),
            }
            if event_type == "llm_call"
            else {"event": event_type}
        ),
        created_at=created_at,
        updated_at=created_at,
    )
    if write_links:
        event.source_links = [
            DecisionTraceSourceLink(trace_event_id=event.id, source_id=item)
            for item in cleaned_source_ids
        ]
    return event


def test_list_decision_traces_filters_by_related_object(client, db):
    now = datetime.now(timezone.utc)
    db.add_all([
        _event(
            event_id="01KTRACEAPI000000000000001",
            related_object_type="chat_message",
            related_object_id="assistant-1",
            event_type="scope",
            created_at=now,
        ),
        _event(
            event_id="01KTRACEAPI000000000000002",
            related_object_type="chat_message",
            related_object_id="assistant-1",
            event_type="llm_call",
            created_at=now + timedelta(seconds=1),
        ),
        _event(
            event_id="01KTRACEAPI000000000000003",
            related_object_type="briefing",
            related_object_id="briefing-1",
            event_type="scope",
            created_at=now + timedelta(seconds=2),
        ),
    ])
    db.commit()

    resp = client.get(
        "/api/v1/decision-traces",
        params={"related_object_type": "chat_message", "related_object_id": "assistant-1"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert [item["event_type"] for item in data["items"]] == ["scope", "llm_call"]
    assert data["items"][1]["model"] == "mock/model"
    assert data["items"][1]["source_ids"] == ["source-a"]
    assert data["items"][1]["payload"] == {"error_type": "rate_limited"}
    assert data["items"][1]["error_type"] == "rate_limited"
    assert "historical provider detail" not in resp.text


def test_list_decision_traces_recent_without_filters(client, db):
    now = datetime.now(timezone.utc)
    db.add_all([
        _event(
            event_id="01KTRACEAPI000000000000004",
            related_object_type="chat_message",
            related_object_id="assistant-old",
            event_type="scope",
            created_at=now,
        ),
        _event(
            event_id="01KTRACEAPI000000000000005",
            related_object_type="chat_message",
            related_object_id="assistant-new",
            event_type="guardrail",
            created_at=now + timedelta(seconds=5),
        ),
    ])
    db.commit()

    resp = client.get("/api/v1/decision-traces", params={"limit": 1})

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["related_object_id"] == "assistant-new"


def test_list_decision_traces_filters_source_id_inside_multi_source_arrays(client, db):
    now = datetime.now(timezone.utc)
    db.add_all([
        _event(
            event_id="01KTRACEAPI000000000000006",
            related_object_type="chat_message",
            related_object_id="assistant-multi-source",
            event_type="scope",
            source_ids=["source-a", "source-b"],
            created_at=now,
        ),
        _event(
            event_id="01KTRACEAPI000000000000007",
            related_object_type="chat_message",
            related_object_id="assistant-single-source",
            event_type="scope",
            source_ids=["source-a"],
            created_at=now + timedelta(seconds=1),
        ),
    ])
    db.commit()

    resp = client.get("/api/v1/decision-traces", params={"source_id": "source-b"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["items"][0]["related_object_id"] == "assistant-multi-source"
    assert data["items"][0]["source_ids"] == ["source-a", "source-b"]


def test_trust_receipt_summarizes_source_coverage_for_related_object(client, db):
    now = datetime.now(timezone.utc)
    db.add_all([
        _event(
            event_id="01KTRACEAPI0000000000000TR1",
            related_object_type="chat_message",
            related_object_id="assistant-trust",
            event_type="scope",
            source_ids=["source-a", "source-b"],
            created_at=now,
        ),
        _event(
            event_id="01KTRACEAPI0000000000000TR2",
            related_object_type="chat_message",
            related_object_id="assistant-trust",
            event_type="retrieval",
            source_ids=["source-b"],
            created_at=now + timedelta(seconds=1),
        ),
        _event(
            event_id="01KTRACEAPI0000000000000TR3",
            related_object_type="chat_message",
            related_object_id="assistant-trust",
            event_type="llm_call",
            source_ids=["source-a"],
            llm_payload_extra={
                "requested_model": "primary/model",
                "configured_fallback_models": ["fallback/model", "emergency/model"],
            },
            created_at=now + timedelta(seconds=2),
        ),
    ])
    db.commit()

    resp = client.get(
        "/api/v1/decision-traces/trust-receipt",
        params=[
            ("related_object_type", "chat_message"),
            ("related_object_id", "assistant-trust"),
            ("expected_source_ids", "source-a"),
            ("expected_source_ids", "source-c"),
        ],
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["related_object_type"] == "chat_message"
    assert data["related_object_id"] == "assistant-trust"
    assert data["event_count"] == 3
    assert data["event_types"] == ["scope", "retrieval", "llm_call"]
    assert data["trace_source_ids"] == ["source-a", "source-b"]
    assert data["llm_source_ids"] == ["source-a"]
    assert data["expected_source_ids"] == ["source-a", "source-c"]
    assert data["covered_expected_source_ids"] == ["source-a"]
    assert data["missing_expected_source_ids"] == ["source-c"]
    assert data["source_coverage_ratio"] == 0.5
    assert data["coverage_status"] == "missing_sources"
    assert data["has_llm_trace"] is True
    assert data["llm_call_count"] == 1
    assert data["models"] == ["mock/model"]
    assert data["requested_models"] == ["primary/model"]
    assert data["configured_fallback_models"] == ["fallback/model", "emergency/model"]


def test_decision_trace_consistency_reports_ok_for_synced_source_links(client, db):
    now = datetime.now(timezone.utc)
    db.add(_event(
        event_id="01KTRACEAPI000000000000008",
        related_object_type="chat_message",
        related_object_id="assistant-consistency-ok",
        event_type="scope",
        source_ids=["source-a", "source-b"],
        created_at=now,
    ))
    db.commit()

    resp = client.get("/api/v1/decision-traces/consistency", params={"sample_limit": 5})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["trace_event_count"] == 1
    assert data["event_source_pair_count"] == 2
    assert data["link_count"] == 2
    assert data["missing_link_count"] == 0
    assert data["orphan_link_count"] == 0
    assert data["sample_missing_links"] == []


def test_decision_trace_consistency_reports_missing_source_links(client, db):
    now = datetime.now(timezone.utc)
    db.add(_event(
        event_id="01KTRACEAPI000000000000009",
        related_object_type="chat_message",
        related_object_id="assistant-consistency-warning",
        event_type="scope",
        source_ids=["source-missing"],
        write_links=False,
        created_at=now,
    ))
    db.commit()

    resp = client.get("/api/v1/decision-traces/consistency", params={"sample_limit": 5})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warning"
    assert data["trace_event_count"] == 1
    assert data["event_source_pair_count"] == 1
    assert data["link_count"] == 0
    assert data["missing_link_count"] == 1
    assert data["orphan_link_count"] == 0
    assert data["sample_missing_links"] == [
        {
            "trace_event_id": "01KTRACEAPI000000000000009",
            "source_id": "source-missing",
        }
    ]


def test_decision_trace_eval_cases_endpoint_returns_trace_derived_cases(client, db):
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-api-eval",
        run_id="run-api-eval",
        session_id="session-api-eval",
        source_ids=["source-api-eval"],
        quality_report={
            "decision_ready": False,
            "warnings": [
                {"code": "prompt_leakage", "message": "raw prompt leaked"},
            ],
        },
        decision_guardrail={
            "status": "blocked_output",
            "reason": "output_integrity_failed",
            "decision_ready": False,
        },
        llm_trace={
            "calls": [
                {
                    "model": "primary-model",
                    "skill_role": "fusion",
                    "duration_ms": 120,
                    "error": "output_integrity_failed",
                    "error_type": "output_integrity_failed",
                },
                {
                    "model": "retry-model",
                    "step": "integrity_retry",
                    "skill_role": "fusion",
                    "duration_ms": 180,
                    "error": "output_integrity_failed",
                    "error_type": "output_integrity_failed",
                },
            ],
            "call_count": 2,
        },
    )
    db.commit()

    resp = client.get("/api/v1/decision-traces/eval-cases", params={"limit": 1})

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    case = data["items"][0]
    assert case["id"] == "trace-chat_message-assistant-api-eval"
    assert case["workflow"] == "chat"
    assert case["coverage_status"] == "generated_from_trace"
    assert case["fixture"] == {
        "type": "decision_trace_replay",
        "related_object_type": "chat_message",
        "related_object_id": "assistant-api-eval",
        "run_id": "run-api-eval",
        "session_id": "session-api-eval",
        "source_ids": ["source-api-eval"],
    }
    assert case["trigger_reasons"] == [
        "quality:prompt_leakage",
        "guardrail:blocked_output",
        "llm:output_integrity_failed",
        "llm:integrity_retry",
    ]
    assert "final answer must not expose blocked model output" in case["acceptance_checks"]


def test_decision_trace_eval_review_manifest_endpoint_is_read_only(client, db):
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-api-review-eval",
        run_id="run-api-review-eval",
        session_id="session-api-review-eval",
        source_ids=["source-api-review"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    db.commit()

    resp = client.get("/api/v1/decision-traces/eval-review-manifest", params={"limit": 3})

    assert resp.status_code == 200
    data = resp.json()
    assert data["report_type"] == "trace_eval_review_manifest"
    assert data["status"] == "review_required"
    assert data["non_destructive"] is True
    assert data["main_manifest_mutated"] is False
    assert data["total_candidates"] == 1
    assert data["candidates"][0]["review_status"] == "pending_review"
    assert data["candidates"][0]["candidate"]["id"] == "trace-chat_message-assistant-api-review-eval"


def test_decision_trace_eval_review_manifest_endpoint_filters_candidates(client, db):
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-api-filter-chat",
        run_id="run-api-filter-chat",
        session_id="session-api-filter-chat",
        source_ids=["source-api-filter-chat"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    record_decision_trace_bundle(
        db,
        related_object_type="briefing",
        related_object_id="briefing-api-filter",
        run_id="run-api-filter-briefing",
        source_ids=["source-api-filter-briefing"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "structured_quality_not_ready", "message": "结构化质量未就绪。"}],
        },
    )
    db.commit()

    resp = client.get(
        "/api/v1/decision-traces/eval-review-manifest",
        params={
            "limit": 5,
            "review_status": "pending_review",
            "workflow": "chat",
            "query": "available_citations",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_candidates"] == 2
    assert data["filtered_candidates"] == 1
    assert data["returned_candidates"] == 1
    assert data["filters"] == {
        "review_status": "pending_review",
        "workflow": "chat",
        "query": "available_citations",
    }
    assert data["status_counts"]["pending_review"] == 2
    assert data["workflow_counts"]["chat"] == 1
    assert data["workflow_counts"]["briefing"] == 1
    assert data["candidates"][0]["candidate"]["id"] == "trace-chat_message-assistant-api-filter-chat"


def test_decision_trace_eval_review_endpoint_persists_review_state(client, db):
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-api-reviewed-eval",
        run_id="run-api-reviewed-eval",
        session_id="session-api-reviewed-eval",
        source_ids=["source-api-reviewed"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    db.commit()

    candidate_id = "trace-chat_message-assistant-api-reviewed-eval"
    resp = client.post(
        f"/api/v1/decision-traces/eval-reviews/{candidate_id}",
        json={
            "review_status": "needs_follow_up",
            "reviewer": "qa",
            "decision_note": "Needs a cleaner fixture before promotion.",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["candidate_id"] == candidate_id
    assert data["review_status"] == "needs_follow_up"
    assert data["reviewer"] == "qa"

    manifest_resp = client.get("/api/v1/decision-traces/eval-review-manifest", params={"limit": 3})
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["pending_candidates"] == 0
    assert manifest["reviewed_candidates"] == 1
    item = manifest["candidates"][0]
    assert item["review_status"] == "needs_follow_up"
    assert item["recommended_next_step"] == "inspect_trace_before_promotion"
    assert item["review_decision"]["decision_note"] == "Needs a cleaner fixture before promotion."


def test_decision_trace_eval_review_promotion_draft_endpoint(client, db, tmp_path, monkeypatch):
    from app.api.v1 import decision_traces as decision_traces_api
    from app.services.trace_eval_review_service import prepare_trace_eval_promotion_draft

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-api-promotion-eval",
        run_id="run-api-promotion-eval",
        session_id="session-api-promotion-eval",
        source_ids=["source-api-promotion"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    db.commit()
    candidate_id = "trace-chat_message-assistant-api-promotion-eval"
    client.post(
        f"/api/v1/decision-traces/eval-reviews/{candidate_id}",
        json={
            "review_status": "approved_for_manifest",
            "reviewer": "qa",
            "decision_note": "Approved from API test.",
        },
    )

    def prepare_with_tmp_paths(db, *, candidate_id: str, reviewer_note: str):
        return prepare_trace_eval_promotion_draft(
            db,
            candidate_id=candidate_id,
            reviewer_note=reviewer_note,
            review_manifest_output_path=tmp_path / "review_manifest.json",
            replay_fixture_output_dir=tmp_path / "fixtures",
            draft_output_path=tmp_path / "draft.json",
        )

    monkeypatch.setattr(
        decision_traces_api,
        "prepare_trace_eval_promotion_draft",
        prepare_with_tmp_paths,
    )

    resp = client.post(
        f"/api/v1/decision-traces/eval-reviews/{candidate_id}/promotion-draft",
        json={"reviewer_note": "Approved from API test."},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft_written"
    assert data["main_manifest_mutated"] is False
    assert data["manifest_validation"]["status"] == "passed"
    assert data["replay_fixture_validation"]["status"] == "passed"
    assert (tmp_path / "fixtures" / f"{candidate_id}.json").exists()
    assert (tmp_path / "draft.json").exists()
