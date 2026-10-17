from __future__ import annotations

from app.services.decision_trace_service import record_decision_trace_bundle
from app.schemas.decision_trace_event import DecisionTraceEvent, DecisionTraceSourceLink


def test_record_decision_trace_bundle_persists_scope_quality_guardrail_and_llm_calls(db):
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-1",
        run_id="run-1",
        session_id="session-1",
        source_ids=["source-a", "source-a", "source-b"],
        scope={"mode": "selected_sources", "source_ids": ["source-a", "source-b"]},
        quality_report={"decision_ready": True, "warnings": []},
        decision_guardrail={"passed": True, "reasons": []},
        llm_trace={
            "calls": [
                {
                    "model": "mock/model-a",
                    "skill_role": "fusion",
                    "duration_ms": 123,
                    "cost_usd": 0.01,
                    "mock": False,
                },
                {
                    "model": "mock/model-b",
                    "skill_role": "review",
                    "duration_ms": 45,
                    "cost_usd": 0.02,
                    "mock": False,
                },
            ],
            "call_count": 2,
        },
    )
    db.commit()

    rows = (
        db.query(DecisionTraceEvent)
        .filter(DecisionTraceEvent.related_object_id == "assistant-1")
        .order_by(DecisionTraceEvent.created_at, DecisionTraceEvent.event_type)
        .all()
    )

    assert [row.event_type for row in rows].count("scope") == 1
    assert [row.event_type for row in rows].count("quality") == 1
    assert [row.event_type for row in rows].count("guardrail") == 1
    assert [row.event_type for row in rows].count("llm_call") == 2
    assert all(row.source_ids == ["source-a", "source-b"] for row in rows)

    llm_rows = [row for row in rows if row.event_type == "llm_call"]
    assert llm_rows[0].model == "mock/model-a"
    assert llm_rows[0].skill_role == "fusion"
    assert llm_rows[0].duration_ms == 123
    assert llm_rows[0].cost_usd == 0.01
    assert llm_rows[1].payload["call_index"] == 2

    links = (
        db.query(DecisionTraceSourceLink)
        .join(DecisionTraceEvent, DecisionTraceEvent.id == DecisionTraceSourceLink.trace_event_id)
        .filter(DecisionTraceEvent.related_object_id == "assistant-1")
        .order_by(DecisionTraceSourceLink.trace_event_id, DecisionTraceSourceLink.source_id)
        .all()
    )
    assert len(links) == 10
    assert {(link.source_id, link.trace_event.event_type) for link in links} == {
        ("source-a", "scope"),
        ("source-b", "scope"),
        ("source-a", "quality"),
        ("source-b", "quality"),
        ("source-a", "guardrail"),
        ("source-b", "guardrail"),
        ("source-a", "llm_call"),
        ("source-b", "llm_call"),
    }


def test_record_decision_trace_bundle_accepts_single_llm_trace(db):
    record_decision_trace_bundle(
        db,
        related_object_type="briefing",
        related_object_id="briefing-1",
        source_ids=["source-a"],
        llm_trace={
            "model": "mock/model",
            "skill_role": "briefing",
            "duration_ms": 200,
            "cost_usd": 0.03,
            "mock": True,
        },
    )
    db.commit()

    row = db.query(DecisionTraceEvent).filter_by(
        related_object_type="briefing",
        related_object_id="briefing-1",
        event_type="llm_call",
    ).one()

    assert row.step_name == "briefing"
    assert row.model == "mock/model"
    assert row.status == "mock"
    assert row.source_ids == ["source-a"]


def test_record_decision_trace_bundle_marks_blocked_output_guardrail_as_warning(db):
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-blocked-output",
        source_ids=["source-a"],
        decision_guardrail={
            "status": "blocked_output",
            "decision_ready": False,
            "reason": "output_integrity_failed",
        },
    )
    db.commit()

    row = db.query(DecisionTraceEvent).filter_by(
        related_object_type="chat_message",
        related_object_id="assistant-blocked-output",
        event_type="guardrail",
    ).one()

    assert row.status == "warning"
