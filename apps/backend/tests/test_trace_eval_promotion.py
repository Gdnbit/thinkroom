from __future__ import annotations

import json

import pytest

from app.services.decision_trace_service import record_decision_trace_bundle
from app.verification.main_workflow_eval_manifest import default_manifest_path
from app.verification.trace_eval_corpus import write_trace_eval_review_manifest
from app.verification.trace_eval_replay import write_trace_eval_replay_fixture


def test_promote_trace_eval_candidate_writes_draft_without_mutating_main_manifest(db, tmp_path):
    from app.verification.trace_eval_promotion import promote_trace_eval_candidate_to_manifest_draft

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-promote-eval",
        run_id="run-promote-eval",
        session_id="session-promote-eval",
        source_ids=["source-promote"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "available_citations_not_used", "message": "引用证据未被使用。"}],
        },
    )
    db.commit()
    review_path = tmp_path / "trace_eval_review_manifest.json"
    write_trace_eval_review_manifest(db, output_path=review_path, limit=5)

    main_manifest_path = default_manifest_path()
    before_main_manifest = main_manifest_path.read_text(encoding="utf-8")
    output_path = tmp_path / "main_workflow_eval_manifest.draft.json"

    report = promote_trace_eval_candidate_to_manifest_draft(
        review_manifest_path=review_path,
        main_manifest_path=main_manifest_path,
        candidate_id="trace-chat_message-assistant-promote-eval",
        command=".\\.venv\\Scripts\\python.exe -m pytest apps/backend/tests/test_trace_eval_corpus.py -q",
        reviewer_note="Promote this real trace as a planned regression candidate after review.",
        output_path=output_path,
    )

    assert main_manifest_path.read_text(encoding="utf-8") == before_main_manifest
    assert output_path.exists()
    assert report["status"] == "draft_written"
    assert report["main_manifest_mutated"] is False
    assert report["manifest_validation"]["status"] == "passed"
    draft = json.loads(output_path.read_text(encoding="utf-8"))
    promoted = [case for case in draft["cases"] if case["id"] == "trace-chat_message-assistant-promote-eval"]
    assert len(promoted) == 1
    case = promoted[0]
    assert case["coverage_status"] == "planned"
    assert case["gap"] == "Trace-derived candidate needs an implemented replay fixture before marking implemented."
    assert case["reviewer_note"] == "Promote this real trace as a planned regression candidate after review."
    assert case["trace_trigger_reasons"] == ["quality:available_citations_not_used"]


def test_promote_trace_eval_candidate_rejects_duplicate_main_manifest_id(tmp_path):
    from app.verification.trace_eval_promotion import promote_trace_eval_candidate_to_manifest_draft

    review_manifest = {
        "report_type": "trace_eval_review_manifest",
        "candidates": [
            {
                "review_status": "pending_review",
                "candidate": {
                    "id": "existing-case",
                    "priority": "P0",
                    "workflow": "chat",
                    "mode": "deterministic",
                    "fixture": {"type": "decision_trace_replay"},
                    "trigger_reasons": ["quality:example"],
                    "acceptance_checks": ["preserve trace", "surface warning"],
                    "evidence_artifacts": ["decision_trace_events"],
                },
            }
        ],
    }
    main_manifest = {
        "manifest_id": "main_workflow_eval_manifest",
        "version": "test",
        "cases": [
            {
                "id": "existing-case",
                "priority": "P0",
                "workflow": "chat",
                "mode": "deterministic",
                "coverage_status": "implemented",
                "fixture": {"type": "existing"},
                "command": ".\\.venv\\Scripts\\python.exe -m pytest apps/backend/tests/test_trace_eval_corpus.py -q",
                "acceptance_checks": ["one", "two"],
                "evidence_artifacts": ["existing"],
            }
        ],
    }
    review_path = tmp_path / "review.json"
    main_path = tmp_path / "main.json"
    output_path = tmp_path / "draft.json"
    review_path.write_text(json.dumps(review_manifest), encoding="utf-8")
    main_path.write_text(json.dumps(main_manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        promote_trace_eval_candidate_to_manifest_draft(
            review_manifest_path=review_path,
            main_manifest_path=main_path,
            candidate_id="existing-case",
            command=".\\.venv\\Scripts\\python.exe -m pytest apps/backend/tests/test_trace_eval_corpus.py -q",
            reviewer_note="duplicate should fail",
            output_path=output_path,
        )

    assert not output_path.exists()


def test_promote_trace_eval_candidate_with_replay_fixture_can_be_implemented(db, tmp_path):
    from app.verification.trace_eval_promotion import promote_trace_eval_candidate_to_manifest_draft

    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id="assistant-implemented-eval",
        run_id="run-implemented-eval",
        session_id="session-implemented-eval",
        source_ids=["source-implemented"],
        quality_report={
            "decision_ready": False,
            "warnings": [{"code": "prompt_leakage", "message": "回答疑似包含内部提示词。"}],
        },
    )
    db.commit()
    review_path = tmp_path / "trace_eval_review_manifest.json"
    write_trace_eval_review_manifest(db, output_path=review_path, limit=5)
    replay_fixture_path = write_trace_eval_replay_fixture(
        db,
        candidate_id="trace-chat_message-assistant-implemented-eval",
        output_dir=tmp_path / "fixtures",
    )
    output_path = tmp_path / "main_workflow_eval_manifest.implemented_draft.json"

    report = promote_trace_eval_candidate_to_manifest_draft(
        review_manifest_path=review_path,
        main_manifest_path=default_manifest_path(),
        candidate_id="trace-chat_message-assistant-implemented-eval",
        command=f".\\.venv\\Scripts\\python.exe -m app.verification.trace_eval_replay --fixture {replay_fixture_path}",
        reviewer_note="Reviewed replay fixture and promoted as executable deterministic trace regression.",
        output_path=output_path,
        coverage_status="implemented",
        replay_fixture_path=replay_fixture_path,
    )

    assert report["status"] == "draft_written"
    assert report["manifest_validation"]["status"] == "passed"
    draft = json.loads(output_path.read_text(encoding="utf-8"))
    promoted = [case for case in draft["cases"] if case["id"] == "trace-chat_message-assistant-implemented-eval"][0]
    assert promoted["coverage_status"] == "implemented"
    assert "gap" not in promoted
    assert promoted["fixture"]["type"] == "decision_trace_replay_fixture"
    assert promoted["fixture"]["path"] == str(replay_fixture_path)
    assert str(replay_fixture_path) in promoted["evidence_artifacts"]
