from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.schemas.decision_trace_event import DecisionTraceEvent
from app.services.decision_trace_service import record_decision_trace_bundle


def test_trace_eval_corpus_extracts_warning_guardrail_and_retry_cases(db):
    from app.verification.trace_eval_corpus import collect_trace_eval_cases

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-eval-1",
        run_id="run-eval-1",
        session_id="session-eval-1",
        source_ids=["source-a"],
        scope={"mode": "selected_sources", "source_ids": ["source-a"]},
        quality_report={
            "decision_ready": False,
            "warnings": [
                {"code": "prompt_leakage", "message": "回答疑似包含内部提示词。"},
                {"code": "language_mismatch", "message": "中文问答中出现大段英文异常片段。"},
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
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-ok",
        run_id="run-ok",
        session_id="session-ok",
        source_ids=["source-a"],
        quality_report={"decision_ready": True, "warnings": []},
        decision_guardrail={"status": "passed"},
        llm_trace={"model": "primary-model", "skill_role": "fusion", "duration_ms": 50},
    )
    db.commit()

    cases = collect_trace_eval_cases(db)

    assert len(cases) == 1
    case = cases[0]
    assert case["id"] == "trace-chat_message-assistant-eval-1"
    assert case["workflow"] == "chat"
    assert case["priority"] == "P0"
    assert case["mode"] == "deterministic"
    assert case["coverage_status"] == "generated_from_trace"
    assert case["fixture"] == {
        "type": "decision_trace_replay",
        "related_object_type": "chat_message",
        "related_object_id": "assistant-eval-1",
        "run_id": "run-eval-1",
        "session_id": "session-eval-1",
        "source_ids": ["source-a"],
    }
    assert case["trigger_reasons"] == [
        "quality:prompt_leakage",
        "quality:language_mismatch",
        "guardrail:blocked_output",
        "llm:output_integrity_failed",
        "llm:integrity_retry",
    ]
    assert case["trace_event_count"] == 5
    assert len(case["trace_event_ids"]) == 5
    assert "final answer must not expose blocked model output" in case["acceptance_checks"]


def test_trace_eval_corpus_limits_and_orders_recent_cases(db):
    from app.verification.trace_eval_corpus import collect_trace_eval_cases

    for idx in range(3):
        record_decision_trace_bundle(
            db,
            related_object_type="briefing",
            related_object_id=f"briefing-eval-{idx}",
            run_id=f"run-eval-{idx}",
            source_ids=[f"source-{idx}"],
            quality_report={
                "decision_ready": False,
                "warnings": [{"code": "source_fallback_used", "message": "使用了资料源原文兜底引用。"}],
            },
            decision_guardrail={"status": "downgraded", "effective_action": "watch"},
        )
    db.commit()

    cases = collect_trace_eval_cases(db, limit=2)

    assert [case["id"] for case in cases] == [
        "trace-briefing-briefing-eval-2",
        "trace-briefing-briefing-eval-1",
    ]


def test_trace_eval_corpus_backfills_complete_event_group_after_recent_seed(db):
    from app.verification.trace_eval_corpus import collect_trace_eval_cases

    now = datetime.now(timezone.utc)
    db.add_all([
        DecisionTraceEvent(
            id=f"01KTRACEFULL{idx:014d}",
            related_object_type="chat_message",
            related_object_id="assistant-full-trace",
            run_id="run-full-trace",
            session_id=None,
            job_id=None,
            event_type="retrieval",
            step_name=f"retrieval-{idx}",
            status="ok",
            source_ids=["source-full"],
            payload={"idx": idx},
            created_at=now + timedelta(seconds=idx),
            updated_at=now + timedelta(seconds=idx),
        )
        for idx in range(60)
    ])
    db.add(DecisionTraceEvent(
        id="01KTRACEFULL99999999999999",
        related_object_type="chat_message",
        related_object_id="assistant-full-trace",
        run_id="run-full-trace",
        session_id=None,
        job_id=None,
        event_type="quality",
        step_name="quality",
        status="warning",
        source_ids=["source-full"],
        payload={
            "decision_ready": False,
            "warnings": [{"code": "source_fallback_used", "message": "使用了资料源原文兜底引用。"}],
        },
        created_at=now + timedelta(seconds=61),
        updated_at=now + timedelta(seconds=61),
    ))
    db.commit()

    cases = collect_trace_eval_cases(db, limit=1)

    assert len(cases) == 1
    case = cases[0]
    assert case["id"] == "trace-chat_message-assistant-full-trace"
    assert case["trace_event_count"] == 61
    assert case["trace_event_ids"][0] == "01KTRACEFULL00000000000000"
    assert case["trace_event_ids"][-1] == "01KTRACEFULL99999999999999"


def test_trace_eval_review_manifest_wraps_candidates_for_human_review(db):
    from app.verification.trace_eval_corpus import build_trace_eval_review_manifest

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-review-eval",
        run_id="run-review-eval",
        session_id="session-review-eval",
        source_ids=["source-review"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    db.commit()

    manifest = build_trace_eval_review_manifest(db, limit=5)

    assert manifest["report_type"] == "trace_eval_review_manifest"
    assert manifest["status"] == "review_required"
    assert manifest["non_destructive"] is True
    assert manifest["main_manifest_mutated"] is False
    assert manifest["source"] == "decision_trace_events"
    assert manifest["total_candidates"] == 1
    item = manifest["candidates"][0]
    assert item["review_status"] == "pending_review"
    assert item["candidate"]["id"] == "trace-chat_message-assistant-review-eval"
    assert item["candidate"]["coverage_status"] == "generated_from_trace"
    assert item["recommended_next_step"] == "review_before_promoting_to_main_manifest"
    assert "do not auto-promote generated trace candidates" in manifest["review_policy"]


def test_trace_eval_review_manifest_marks_cases_already_in_main_manifest(db, tmp_path):
    from app.verification.trace_eval_corpus import build_trace_eval_review_manifest

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-covered-eval",
        run_id="run-covered-eval",
        session_id="session-covered-eval",
        source_ids=["source-covered"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    db.commit()

    main_manifest_path = tmp_path / "main_workflow_eval_manifest.json"
    main_manifest_path.write_text(
        json.dumps({"cases": [{"id": "trace-chat_message-assistant-covered-eval"}]}),
        encoding="utf-8",
    )

    manifest = build_trace_eval_review_manifest(db, limit=5, main_manifest_path=main_manifest_path)

    assert manifest["status"] == "covered"
    assert manifest["pending_candidates"] == 0
    assert manifest["covered_candidates"] == 1
    item = manifest["candidates"][0]
    assert item["review_status"] == "already_covered"
    assert item["recommended_next_step"] == "no_action_required_trace_already_in_main_manifest"
    assert item["main_manifest_case_id"] == "trace-chat_message-assistant-covered-eval"


def test_trace_eval_review_manifest_uses_persisted_review_decision(db):
    from app.services.trace_eval_review_service import upsert_trace_eval_review
    from app.verification.trace_eval_corpus import build_trace_eval_review_manifest

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-ignored-eval",
        run_id="run-ignored-eval",
        session_id="session-ignored-eval",
        source_ids=["source-ignored"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    upsert_trace_eval_review(
        db,
        candidate_id="trace-chat_message-assistant-ignored-eval",
        review_status="ignored",
        reviewer="qa",
        decision_note="Duplicate trace; keep as record but do not promote.",
    )
    db.commit()

    manifest = build_trace_eval_review_manifest(db, limit=5)

    assert manifest["status"] == "reviewed"
    assert manifest["pending_candidates"] == 0
    assert manifest["reviewed_candidates"] == 1
    item = manifest["candidates"][0]
    assert item["review_status"] == "ignored"
    assert item["recommended_next_step"] == "no_action_required_trace_ignored"
    assert item["review_decision"]["reviewer"] == "qa"
    assert item["review_decision"]["decision_note"] == "Duplicate trace; keep as record but do not promote."


def test_trace_eval_review_manifest_filters_and_paginates_candidates(db):
    from app.services.trace_eval_review_service import upsert_trace_eval_review
    from app.verification.trace_eval_corpus import build_trace_eval_review_manifest

    for idx in range(3):
        record_decision_trace_bundle(
            db,
            related_object_type="chat_message",
            related_object_id=f"assistant-filter-eval-{idx}",
            run_id=f"run-filter-eval-{idx}",
            session_id=f"session-filter-eval-{idx}",
            source_ids=[f"source-filter-{idx}"],
            quality_report={
                "decision_ready": False,
                "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
            },
            decision_guardrail={"status": "warn_only", "decision_ready": False},
        )
    record_decision_trace_bundle(
        db,
        related_object_type="briefing",
        related_object_id="briefing-filter-eval",
        run_id="run-briefing-filter-eval",
        source_ids=["source-briefing-filter"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "structured_quality_not_ready", "message": "结构化质量未就绪。"}],
        },
    )
    upsert_trace_eval_review(
        db,
        candidate_id="trace-briefing-briefing-filter-eval",
        review_status="ignored",
        reviewer="qa",
        decision_note="Not useful for the curated manifest.",
    )
    db.commit()

    manifest = build_trace_eval_review_manifest(
        db,
        limit=2,
        offset=1,
        review_status="pending_review",
        workflow="chat",
        query="available_citations",
    )

    assert manifest["total_candidates"] == 4
    assert manifest["filtered_candidates"] == 3
    assert manifest["returned_candidates"] == 2
    assert manifest["limit"] == 2
    assert manifest["offset"] == 1
    assert manifest["filters"] == {
        "review_status": "pending_review",
        "workflow": "chat",
        "query": "available_citations",
    }
    assert manifest["status_counts"]["pending_review"] == 3
    assert manifest["status_counts"]["ignored"] == 1
    assert manifest["workflow_counts"]["chat"] == 3
    assert manifest["workflow_counts"]["briefing"] == 1
    assert [item["candidate"]["workflow"] for item in manifest["candidates"]] == ["chat", "chat"]
    assert all(item["review_status"] == "pending_review" for item in manifest["candidates"])


def test_trace_eval_review_manifest_can_be_written_to_json_file(db, tmp_path):
    from app.verification.trace_eval_corpus import write_trace_eval_review_manifest

    record_decision_trace_bundle(
        db,
        related_object_type="briefing",
        related_object_id="briefing-review-eval",
        run_id="run-briefing-review-eval",
        source_ids=["source-review"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "structured_quality_not_ready", "message": "结构化质量未就绪。"}],
        },
    )
    db.commit()

    output_path = tmp_path / "nested" / "trace_eval_review_manifest.json"
    manifest = write_trace_eval_review_manifest(db, output_path=output_path, limit=3)

    assert output_path.exists()
    assert manifest["output_path"] == str(output_path)
    written = output_path.read_text(encoding="utf-8")
    assert "trace_eval_review_manifest" in written
    assert "briefing-review-eval" in written
