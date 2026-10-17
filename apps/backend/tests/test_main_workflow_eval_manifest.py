from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "evals" / "main-workflow" / "manifest.json"


REQUIRED_CASE_IDS = {
    "selected-source-chat-cites-only-selected-source",
    "unsupported-question-refuses-insufficient-evidence",
    "selected-source-refuses-when-structured-claim-validity-window-is-not-active",
    "briefing-renders-required-sections",
    "briefing-selected-source-refuses-when-actionable-evidence-missing",
    "briefing-detail-preserves-quality-filters-and-source-scope",
    "committee-selected-source-refuses-when-actionable-evidence-missing",
    "committee-detail-preserves-quality-filters-and-source-scope",
    "committee-detail-renders-trust-receipt-and-source-scope",
    "committee-records-seat-traces",
    "upload-ingestion-progress-reaches-source-id",
    "answer-output-shape-no-raw-json-or-prompt-leak",
    "answer-integrity-blocks-uncited-material-claims",
    "decision-quality-controlled-contract-validates",
    "frontend-production-build-passes",
    "live-smoke-records-llm-trace-cost-tokens",
    "real-scoped-chat-records-reference-time-and-citations",
    "trace-debate-synthetic-demo",
}


def test_main_workflow_eval_manifest_defines_production_quality_contract():
    from app.verification.main_workflow_eval_manifest import load_manifest, validate_manifest

    manifest = load_manifest(MANIFEST_PATH)
    report = validate_manifest(manifest)

    assert report["status"] == "passed"
    assert report["total_cases"] >= 7
    assert report["deterministic_cases"] >= 6
    assert report["live_optional_cases"] >= 2
    assert REQUIRED_CASE_IDS.issubset(set(report["case_ids"]))
    assert report["missing_required_cases"] == []

    for case in manifest["cases"]:
        assert case["id"]
        assert case["priority"] in {"P0", "P1"}
        assert case["workflow"] in {
            "ingestion",
            "chat",
            "briefing",
            "committee",
            "answer_integrity",
            "frontend",
            "live_smoke",
        }
        assert case["mode"] in {"deterministic", "live_optional"}
        assert case["fixture"]["type"]
        assert case["command"]
        assert len(case["acceptance_checks"]) >= 2
        assert len(case["evidence_artifacts"]) >= 1

    answer_integrity = next(case for case in manifest["cases"] if case["id"] == "answer-output-shape-no-raw-json-or-prompt-leak")
    joined_checks = "\n".join(answer_integrity["acceptance_checks"]).lower()
    assert "reference_time" in joined_checks
    assert "answer content" in joined_checks

    uncited_material = next(
        case for case in manifest["cases"] if case["id"] == "answer-integrity-blocks-uncited-material-claims"
    )
    assert uncited_material["workflow"] == "answer_integrity"
    joined_uncited_checks = "\n".join(uncited_material["acceptance_checks"]).lower()
    assert "uncited_material_claim" in joined_uncited_checks
    assert "decision_ready" in joined_uncited_checks

    frontend_build = next(case for case in manifest["cases"] if case["id"] == "frontend-production-build-passes")
    assert frontend_build["workflow"] == "frontend"
    assert frontend_build["mode"] == "deterministic"
    assert frontend_build["command"] == "npm.cmd run build"
    joined_frontend_checks = "\n".join(frontend_build["acceptance_checks"]).lower()
    assert "typescript" in joined_frontend_checks
    assert "vite" in joined_frontend_checks

    real_scoped_chat = next(
        case for case in manifest["cases"] if case["id"] == "real-scoped-chat-records-reference-time-and-citations"
    )
    assert real_scoped_chat["mode"] == "live_optional"
    assert "app.verification.real_scope_chat_acceptance" in real_scoped_chat["command"]
    joined_real_checks = "\n".join(real_scoped_chat["acceptance_checks"]).lower()
    assert "reference_time" in joined_real_checks
    assert "citation" in joined_real_checks

    trace_replay = next(
        case for case in manifest["cases"] if case["id"] == "trace-debate-synthetic-demo"
    )
    assert trace_replay["workflow"] == "committee"
    assert trace_replay["mode"] == "deterministic"
    assert trace_replay["coverage_status"] == "implemented"
    assert trace_replay["fixture"]["type"] == "decision_trace_replay_fixture"
    assert "trace_eval_replay" in trace_replay["command"]
    assert "trace_eval_replay_fixtures" in trace_replay["fixture"]["path"]


def test_main_workflow_eval_manifest_cli_writes_report(tmp_path):
    from app.verification.main_workflow_eval_manifest import run_manifest_validation

    report_path = tmp_path / "manifest-report.json"
    report = run_manifest_validation(manifest_path=MANIFEST_PATH, report_path=report_path)

    assert report["status"] == "passed"
    assert report_path.exists()
    assert "main_workflow_eval_manifest" in report_path.read_text(encoding="utf-8")
