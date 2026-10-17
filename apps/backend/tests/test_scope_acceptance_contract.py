from __future__ import annotations

from app.verification.scope_acceptance import run_acceptance


def test_scope_acceptance_covers_main_flow_evidence_and_retrieval_trace(client):
    assert client.get("/health").status_code == 200

    report = run_acceptance(write_report=False)

    assertions = {item["name"]: item for item in report["assertions"]}
    required = {
        "chat_evidence_payload_ready",
        "chat_timing_trace_visible",
        "briefing_evidence_payload_ready",
        "briefing_timing_trace_visible",
        "committee_evidence_payload_ready",
        "committee_timing_trace_visible",
        "ui_chat_session_contains_chat_briefing_committee_messages",
        "chat_decision_trace_persisted",
        "briefing_decision_trace_persisted",
        "committee_decision_trace_persisted",
    }

    assert report["status"] == "passed"
    assert required.issubset(assertions)
    for name in required:
        assert assertions[name]["status"] == "passed"

    ui_session = report["snapshots"]["ui_session"]
    assert ui_session["session_id"]
    assert {"chat", "briefing", "debate"}.issubset(set(ui_session["assistant_modes"]))

    for trace_name in (
        "chat_decision_trace_persisted",
        "briefing_decision_trace_persisted",
        "committee_decision_trace_persisted",
    ):
        assert "retrieval" in assertions[trace_name]["details"]["event_types"]
