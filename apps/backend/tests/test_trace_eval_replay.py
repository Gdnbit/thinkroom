from __future__ import annotations

import json

from app.services.decision_trace_service import record_decision_trace_bundle


def test_trace_eval_replay_fixture_exports_complete_self_contained_trace(db, tmp_path):
    from app.verification.trace_eval_replay import (
        validate_trace_eval_replay_fixture,
        write_trace_eval_replay_fixture,
    )

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-replay-eval",
        run_id="run-replay-eval",
        session_id="session-replay-eval",
        source_ids=["source-replay"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "prompt_leakage", "message": "回答疑似包含内部提示词。"}],
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

    output_path = write_trace_eval_replay_fixture(
        db,
        candidate_id="trace-chat_message-assistant-replay-eval",
        output_dir=tmp_path,
    )

    assert output_path.exists()
    fixture = json.loads(output_path.read_text(encoding="utf-8"))
    assert fixture["fixture_type"] == "decision_trace_replay_fixture"
    assert fixture["candidate_id"] == "trace-chat_message-assistant-replay-eval"
    assert fixture["expected"]["trace_event_count"] == 4
    assert fixture["expected"]["source_ids"] == ["source-replay"]
    assert fixture["expected"]["trigger_reasons"] == [
        "quality:prompt_leakage",
        "guardrail:blocked_output",
        "llm:output_integrity_failed",
        "llm:integrity_retry",
    ]
    assert len(fixture["events"]) == 4
    assert fixture["events"][0]["related_object_type"] == "chat_message"
    assert any(event["event_type"] == "guardrail" for event in fixture["events"])

    report = validate_trace_eval_replay_fixture(output_path)
    assert report["status"] == "passed"
    assert report["errors"] == []
    assert report["candidate_id"] == "trace-chat_message-assistant-replay-eval"
    assert report["trace_event_count"] == 4


def test_trace_eval_replay_fixture_validation_fails_when_event_snapshot_is_tampered(db, tmp_path):
    from app.verification.trace_eval_replay import (
        validate_trace_eval_replay_fixture,
        write_trace_eval_replay_fixture,
    )

    record_decision_trace_bundle(
        db,
        related_object_type="briefing",
        related_object_id="briefing-replay-eval",
        run_id="run-briefing-replay-eval",
        source_ids=["source-briefing"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "structured_quality_not_ready", "message": "结构化质量未就绪。"}],
        },
    )
    db.commit()
    output_path = write_trace_eval_replay_fixture(
        db,
        candidate_id="trace-briefing-briefing-replay-eval",
        output_dir=tmp_path,
    )
    fixture = json.loads(output_path.read_text(encoding="utf-8"))
    fixture["events"].pop()
    output_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")

    report = validate_trace_eval_replay_fixture(output_path)

    assert report["status"] == "failed"
    assert "trace_event_count mismatch" in report["errors"]
