from __future__ import annotations

import json

import pytest

from app.services.decision_trace_service import record_decision_trace_bundle


def test_prepare_trace_eval_promotion_draft_requires_approved_review(db, tmp_path):
    from app.services.trace_eval_review_service import prepare_trace_eval_promotion_draft

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-not-approved-eval",
        run_id="run-not-approved-eval",
        session_id="session-not-approved-eval",
        source_ids=["source-not-approved"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    db.commit()

    with pytest.raises(ValueError, match="approved_for_manifest"):
        prepare_trace_eval_promotion_draft(
            db,
            candidate_id="trace-chat_message-assistant-not-approved-eval",
            reviewer_note="not approved yet",
            review_manifest_output_path=tmp_path / "review_manifest.json",
            replay_fixture_output_dir=tmp_path / "fixtures",
            draft_output_path=tmp_path / "draft.json",
        )


def test_prepare_trace_eval_promotion_draft_exports_fixture_and_implemented_draft(db, tmp_path):
    from app.services.trace_eval_review_service import (
        prepare_trace_eval_promotion_draft,
        upsert_trace_eval_review,
    )

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-approved-eval",
        run_id="run-approved-eval",
        session_id="session-approved-eval",
        source_ids=["source-approved"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
        decision_guardrail={"status": "warn_only", "decision_ready": False},
    )
    candidate_id = "trace-chat_message-assistant-approved-eval"
    upsert_trace_eval_review(
        db,
        candidate_id=candidate_id,
        review_status="approved_for_manifest",
        reviewer="qa",
        decision_note="Approved as deterministic regression candidate.",
    )
    db.commit()

    report = prepare_trace_eval_promotion_draft(
        db,
        candidate_id=candidate_id,
        reviewer_note="Approved as deterministic regression candidate.",
        review_manifest_output_path=tmp_path / "trace_eval_review_manifest.json",
        replay_fixture_output_dir=tmp_path / "fixtures",
        draft_output_path=tmp_path / "main_workflow_eval_manifest.trace_draft.json",
    )

    assert report["status"] == "draft_written"
    assert report["main_manifest_mutated"] is False
    assert report["manifest_validation"]["status"] == "passed"
    assert report["replay_fixture_validation"]["status"] == "passed"
    assert report["candidate_id"] == candidate_id

    replay_fixture_path = report["replay_fixture_path"]
    draft_output_path = report["output_path"]
    assert replay_fixture_path.endswith(f"{candidate_id}.json")
    assert (tmp_path / "fixtures" / f"{candidate_id}.json").exists()
    assert (tmp_path / "main_workflow_eval_manifest.trace_draft.json").exists()

    draft = json.loads((tmp_path / "main_workflow_eval_manifest.trace_draft.json").read_text(encoding="utf-8"))
    promoted = [case for case in draft["cases"] if case["id"] == candidate_id][0]
    assert promoted["coverage_status"] == "implemented"
    assert promoted["fixture"]["type"] == "decision_trace_replay_fixture"
    assert promoted["fixture"]["candidate_id"] == candidate_id
    assert "gap" not in promoted

    review = report["review_decision"]
    assert review["candidate_id"] == candidate_id
    assert review["review_metadata"]["promotion_draft"]["output_path"] == draft_output_path
    assert review["review_metadata"]["promotion_draft"]["replay_fixture_path"] == replay_fixture_path
