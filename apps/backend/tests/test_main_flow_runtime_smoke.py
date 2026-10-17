from __future__ import annotations


def test_write_report_updates_stable_latest_copy(tmp_path):
    from app.verification.main_flow_runtime_smoke import write_report

    report = {
        "report_type": "main_flow_runtime_smoke",
        "generated_at": "2026-07-09T00:00:00+00:00",
        "assessment": {"status": "passed", "errors": [], "warnings": []},
    }
    output = tmp_path / "main_flow_runtime_smoke_20260709T000000Z.json"

    written = write_report(report, output)

    latest = tmp_path / "main_flow_runtime_smoke_latest.json"
    assert written == output
    assert output.is_file()
    assert latest.is_file()
    assert latest.read_text(encoding="utf-8") == output.read_text(encoding="utf-8")


def test_assess_runtime_smoke_report_requires_all_main_flow_surfaces():
    from app.verification.main_flow_runtime_smoke import assess_runtime_smoke_report

    report = {
        "session_id": "session-smoke",
        "steps": {
            "health": {"ok": True, "status": 200},
            "create_session": {"ok": True, "session_id": "session-smoke"},
            "chat": {
                "ok": True,
                "status": 200,
                "message_id": "chat-message-1",
                "content_length": 240,
                "citations_count": 2,
                "quality_report_present": True,
                "decision_guardrail_present": True,
                "guardrail_status": "passed",
                "guardrail_decision_ready": True,
                "quality_severity": "ok",
                "quality_decision_ready": True,
                "reference_time_present": True,
                "timing_trace": {"total_ms": 900, "retrieval_ms": 100, "model_calls_ms": 500},
            },
            "messages": {
                "ok": True,
                "count": 2,
                "roles": ["user", "assistant"],
                "assistant_payload_keys": ["quality_report", "decision_guardrail", "citations", "run_id", "timing_trace"],
            },
            "chat_trace": {
                "ok": True,
                "count": 5,
                "event_types": ["retrieval", "scope", "quality", "guardrail", "llm_call"],
            },
            "briefing": {
                "ok": True,
                "status": 200,
                "id": "briefing-1",
                "summary_length": 80,
                "quality_report_present": True,
                "decision_guardrail_present": True,
                "quality_severity": "ok",
                "quality_decision_ready": True,
                "guardrail_status": "passed",
                "guardrail_decision_ready": True,
                "timing_trace": {"total_ms": 1_100, "retrieval_ms": 120, "model_calls_ms": 700},
            },
            "briefing_trace": {
                "ok": True,
                "count": 4,
                "event_types": ["retrieval", "scope", "quality", "guardrail"],
            },
            "committee": {
                "ok": True,
                "status": 200,
                "debate_id": "debate-1",
                "synthesis_length": 120,
                "decision_guardrail_present": True,
                "timing_trace": {"total_ms": 1_000, "model_calls_ms": 500, "other_processing_ms": 500},
            },
            "committee_detail": {
                "ok": True,
                "status": 200,
                "round_count": 4,
                "adjudication_synthesis_length": 120,
                "timing_trace": {"total_ms": 1_000, "model_calls_ms": 500, "other_processing_ms": 500},
            },
            "committee_trace": {
                "ok": True,
                "count": 6,
                "event_types": ["retrieval", "scope", "quality", "guardrail", "llm_call"],
            },
        },
    }

    result = assess_runtime_smoke_report(report, require_committee=True)

    assert result["status"] == "passed"
    assert result["errors"] == []

    report["steps"]["chat"].pop("timing_trace")
    result = assess_runtime_smoke_report(report, require_committee=True)
    assert "chat.timing_trace_missing" in result["errors"]

    report["steps"]["chat"]["timing_trace"] = {
        "total_ms": 900,
        "retrieval_ms": 100,
        "model_calls_ms": 500,
    }
    report["steps"]["briefing"].pop("timing_trace")
    result = assess_runtime_smoke_report(report, require_committee=True)
    assert "briefing.timing_trace_missing" in result["errors"]


def test_assess_runtime_smoke_report_requires_auditable_real_calls_for_selected_source():
    from app.verification.main_flow_runtime_smoke import assess_runtime_smoke_report

    source_id = "source-real-smoke"

    def real_trace(model: str) -> dict:
        return {
            "call_count": 1,
            "total_cost_usd": 0.01,
            "calls": [{
                "model": model,
                "requested_model": f"requested-{model}",
                "configured_fallback_models": [f"fallback-{model}"],
                "mock": False,
                "cost_usd": 0.01,
                "cost_source": "provider_usage",
                "generation_id": f"gen-{model}",
            }],
        }

    def route_identity(scope: str) -> dict:
        return {
            "schema_version": 1,
            "scope": scope,
            "contract_version": f"{scope}-v1",
            "fingerprint": f"sha256:{scope:0<64}"[:71],
            "configuration_fingerprint": "sha256:" + "a" * 64,
            "implementation_fingerprint": "sha256:" + "b" * 64,
        }

    report = {
        "session_id": "session-real-smoke",
        "selected_source_id": source_id,
        "steps": {
            "health": {"ok": True, "status": 200},
            "create_session": {"ok": True, "session_id": "session-real-smoke"},
            "chat": {
                "ok": True,
                "status": 200,
                "message_id": "chat-real-1",
                "content_length": 240,
                "citations_count": 1,
                "citation_source_ids": [source_id],
                "scope": {"mode": "selected_sources", "source_ids": [source_id]},
                "quality_report_present": True,
                "decision_guardrail_present": True,
                "guardrail_status": "passed",
                "guardrail_decision_ready": True,
                "quality_severity": "ok",
                "quality_decision_ready": True,
                "reference_time_present": True,
                "llm_trace": real_trace("chat-model"),
                "route_identity": route_identity("decision_chat_selected_source"),
                "timing_trace": {"total_ms": 900, "retrieval_ms": 100, "model_calls_ms": 500},
            },
            "messages": {
                "ok": True,
                "count": 2,
                "roles": ["user", "assistant"],
                "assistant_payload_keys": ["quality_report", "decision_guardrail", "run_id", "timing_trace"],
            },
            "chat_trace": {
                "ok": True,
                "count": 5,
                "event_types": ["retrieval", "scope", "quality", "guardrail", "llm_call"],
            },
            "briefing": {
                "ok": True,
                "status": 200,
                "id": "briefing-real-1",
                "summary_length": 80,
                "scope": {"mode": "selected_sources", "source_ids": [source_id]},
                "claims_used": 1,
                "quality_report_present": True,
                "decision_guardrail_present": True,
                "quality_severity": "ok",
                "quality_decision_ready": True,
                "guardrail_status": "passed",
                "guardrail_decision_ready": True,
                "llm_trace": real_trace("briefing-model"),
                "route_identity": route_identity("briefing_selected_source"),
                "timing_trace": {"total_ms": 1_100, "retrieval_ms": 120, "model_calls_ms": 700},
            },
            "briefing_trace": {
                "ok": True,
                "count": 5,
                "event_types": ["retrieval", "scope", "quality", "guardrail", "llm_call"],
            },
            "committee": {
                "ok": True,
                "status": 200,
                "debate_id": "debate-real-1",
                "synthesis_length": 120,
                "scope": {"mode": "selected_sources", "source_ids": [source_id]},
                "decision_guardrail_present": True,
                "llm_trace": real_trace("committee-model"),
                "route_identity": route_identity("committee_selected_source"),
                "quality_report_present": True,
                "decision_guardrail_present": True,
                "quality_severity": "warning",
                "quality_decision_ready": False,
                "guardrail_status": "warn_only",
                "guardrail_decision_ready": False,
                "guardrail_effective_action": "watch",
                "guardrail_actionable_action_detected": False,
                "timing_trace": {"total_ms": 1_000, "model_calls_ms": 500, "other_processing_ms": 500},
            },
            "committee_detail": {
                "ok": True,
                "status": 200,
                "round_count": 2,
                "adjudication_synthesis_length": 120,
                "route_identity": route_identity("committee_selected_source"),
                "quality_report_present": True,
                "decision_guardrail_present": True,
                "quality_severity": "warning",
                "quality_decision_ready": False,
                "guardrail_status": "warn_only",
                "guardrail_decision_ready": False,
                "guardrail_effective_action": "watch",
                "timing_trace": {"total_ms": 1_000, "model_calls_ms": 500, "other_processing_ms": 500},
            },
            "committee_trace": {
                "ok": True,
                "count": 5,
                "event_types": ["retrieval", "scope", "quality", "guardrail", "llm_call"],
            },
        },
    }

    result = assess_runtime_smoke_report(report, require_committee=True, require_real=True)
    assert result["status"] == "passed"

    report["steps"]["briefing"]["quality_severity"] = "warning"
    result = assess_runtime_smoke_report(report, require_committee=True, require_real=True)
    assert "briefing.quality_not_ok" in result["errors"]
    report["steps"]["briefing"]["quality_severity"] = "ok"

    report["steps"]["committee"]["quality_report_present"] = False
    result = assess_runtime_smoke_report(report, require_committee=True, require_real=True)
    assert "committee.quality_report_missing" in result["errors"]
    report["steps"]["committee"]["quality_report_present"] = True

    report["steps"]["committee"]["guardrail_effective_action"] = "enter"
    result = assess_runtime_smoke_report(report, require_committee=True, require_real=True)
    assert "committee.quality_boundary_unsafe_or_inconsistent" in result["errors"]
    report["steps"]["committee"]["guardrail_effective_action"] = "watch"

    report["steps"]["briefing"]["llm_trace"]["calls"][0]["generation_id"] = None
    result = assess_runtime_smoke_report(report, require_committee=True, require_real=True)
    assert result["status"] == "failed"
    assert "briefing.call_1.generation_id_missing" in result["errors"]

    report["steps"]["briefing"]["llm_trace"]["calls"][0]["generation_id"] = "gen-briefing-model"
    report["steps"]["committee"]["route_identity"] = None
    result = assess_runtime_smoke_report(report, require_committee=True, require_real=True)
    assert result["status"] == "failed"
    assert "committee.route_identity_missing" in result["errors"]


def test_assess_real_runtime_smoke_requires_requested_and_fallback_models():
    from app.verification.main_flow_runtime_smoke import _validate_real_llm_calls

    step = {
        "llm_trace": {
            "call_count": 1,
            "calls": [{
                "model": "actual/model",
                "mock": False,
                "cost_usd": 0.01,
                "cost_source": "provider_usage",
                "generation_id": "gen-1",
            }],
        }
    }
    errors: list[str] = []

    _validate_real_llm_calls(step, "briefing", errors)

    assert "briefing.call_1.requested_model_missing" in errors
    assert "briefing.call_1.configured_fallback_models_missing" in errors


def test_chat_step_persists_hash_not_private_answer_excerpt():
    from app.verification.main_flow_runtime_smoke import _chat_step

    step = _chat_step({
        "ok": True,
        "status": 200,
        "duration_ms": 10,
        "body": {"id": "message-1", "content": "private selected-source answer"},
    })

    assert "content_head" not in step
    assert step["content_length"] == len("private selected-source answer")
    assert step["content_hash"].startswith("sha256:")


def test_assess_runtime_smoke_report_blocks_missing_trace_and_unrenderable_answer():
    from app.verification.main_flow_runtime_smoke import assess_runtime_smoke_report

    report = {
        "session_id": "session-smoke",
        "steps": {
            "health": {"ok": True, "status": 200},
            "create_session": {"ok": True, "session_id": "session-smoke"},
            "chat": {
                "ok": True,
                "status": 200,
                "message_id": "chat-message-1",
                "content_length": 12,
                "citations_count": 0,
                "quality_report_present": False,
                "decision_guardrail_present": False,
                "guardrail_status": "blocked_output",
                "reference_time_present": False,
            },
            "messages": {"ok": True, "count": 1, "roles": ["user"], "assistant_payload_keys": []},
            "chat_trace": {"ok": True, "count": 0, "event_types": []},
            "briefing": {"ok": False, "status": 422},
            "briefing_trace": {"ok": True, "count": 0, "event_types": []},
        },
    }

    result = assess_runtime_smoke_report(report, require_committee=False)

    assert result["status"] == "failed"
    assert "chat.content_length_too_short" in result["errors"]
    assert "chat.reference_time_missing" in result["errors"]
    assert "chat.output_blocked" in result["errors"]
    assert "messages.assistant_message_missing" in result["errors"]
    assert "chat_trace.missing_event:quality" in result["errors"]
    assert "briefing.request_failed" in result["errors"]


def test_assess_runtime_smoke_report_rejects_uncited_answer_without_visible_caution():
    from app.verification.main_flow_runtime_smoke import assess_runtime_smoke_report

    report = {
        "session_id": "session-smoke",
        "steps": {
            "health": {"ok": True, "status": 200},
            "create_session": {"ok": True, "session_id": "session-smoke"},
            "chat": {
                "ok": True,
                "status": 200,
                "message_id": "chat-message-1",
                "content_length": 180,
                "content_head": "截至2026年07月05日 15:39，核心交易撮合与清算逻辑运行稳定，前端延迟约200ms。",
                "citations_count": 0,
                "quality_report_present": True,
                "decision_guardrail_present": True,
                "reference_time_present": True,
            },
            "messages": {
                "ok": True,
                "count": 2,
                "roles": ["user", "assistant"],
                "assistant_payload_keys": ["quality_report", "decision_guardrail", "run_id"],
            },
            "chat_trace": {
                "ok": True,
                "count": 5,
                "event_types": ["retrieval", "scope", "quality", "guardrail", "llm_call"],
            },
            "briefing": {
                "ok": True,
                "status": 200,
                "id": "briefing-1",
                "summary_length": 80,
                "quality_report_present": True,
                "decision_guardrail_present": True,
            },
            "briefing_trace": {
                "ok": True,
                "count": 4,
                "event_types": ["retrieval", "scope", "quality", "guardrail"],
            },
        },
    }

    result = assess_runtime_smoke_report(report, require_committee=False)

    assert result["status"] == "failed"
    assert "chat.uncited_answer_lacks_visible_caution" in result["errors"]
