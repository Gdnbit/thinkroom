from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "evals" / "main-workflow" / "manifest.json"


def test_main_workflow_eval_runner_builds_unique_deterministic_plan():
    from app.verification.main_workflow_eval_manifest import load_manifest
    from app.verification.main_workflow_eval_runner import build_execution_plan

    manifest = load_manifest(MANIFEST_PATH)
    plan = build_execution_plan(manifest, repo_root=REPO_ROOT, include_live=False)

    assert plan.total_cases == 23
    assert plan.included_cases == 21
    assert plan.skipped_live_cases == 2
    assert plan.unique_command_count < plan.included_cases
    assert all(step.mode == "deterministic" for step in plan.steps)
    assert all(step.cwd.is_absolute() for step in plan.steps)
    pytest_steps = [step for step in plan.steps if " -m pytest " in step.command]
    assert pytest_steps
    assert all(step.env["PYTEST_DATABASE_URL"].startswith("sqlite:///") for step in pytest_steps)
    assert all("main_workflow_eval_" in step.env["PYTEST_DATABASE_URL"] for step in pytest_steps)
    assert any(
        "selected-source-chat-cites-only-selected-source" in step.case_ids
        and step.cwd == REPO_ROOT / "apps" / "backend"
        and step.command.startswith("..\\..\\.venv\\Scripts\\python.exe -m app.verification.scope_acceptance")
        for step in plan.steps
    )
    assert any(
        "committee-detail-renders-trust-receipt-and-source-scope" in step.case_ids
        and step.cwd == REPO_ROOT / "apps" / "frontend"
        and step.command == "npm.cmd run ui:acceptance"
        and {dependency["name"] for dependency in step.service_dependencies} == {"backend", "frontend"}
        for step in plan.steps
    )
    assert any(
        "decision-quality-controlled-contract-validates" in step.case_ids
        and step.cwd == REPO_ROOT / "apps" / "backend"
        and step.command.startswith("..\\..\\.venv\\Scripts\\python.exe -m app.verification.decision_quality_eval")
        for step in plan.steps
    )
    assert any(
        "frontend-production-build-passes" in step.case_ids
        and step.cwd == REPO_ROOT / "apps" / "frontend"
        and step.command == "npm.cmd run build"
        and step.service_dependencies == []
        for step in plan.steps
    )
    assert any(
        "trace-debate-synthetic-demo" in step.case_ids
        and step.cwd == REPO_ROOT / "apps" / "backend"
        and step.command.startswith("..\\..\\.venv\\Scripts\\python.exe -m app.verification.trace_eval_replay")
        and "trace_eval_replay_fixtures" in step.command
        for step in plan.steps
    )
    assert not any("live-smoke-records-llm-trace-cost-tokens" in step.case_ids for step in plan.steps)
    assert not any("real-scoped-chat-records-reference-time-and-citations" in step.case_ids for step in plan.steps)


def test_main_workflow_eval_runner_overrides_external_pytest_database_url(monkeypatch):
    from app.verification.main_workflow_eval_manifest import load_manifest
    from app.verification.main_workflow_eval_runner import build_execution_plan

    monkeypatch.setenv("PYTEST_DATABASE_URL", "sqlite:///external_full_pytest.db")

    manifest = load_manifest(MANIFEST_PATH)
    plan = build_execution_plan(manifest, repo_root=REPO_ROOT, include_live=False)
    pytest_steps = [step for step in plan.steps if " -m pytest " in step.command]

    assert pytest_steps
    assert all("main_workflow_eval_" in step.env["PYTEST_DATABASE_URL"] for step in pytest_steps)
    assert not any("external_full_pytest.db" in step.env["PYTEST_DATABASE_URL"] for step in pytest_steps)


def test_main_workflow_eval_runner_includes_real_scoped_chat_live_case_when_requested():
    from app.verification.main_workflow_eval_manifest import load_manifest
    from app.verification.main_workflow_eval_runner import build_execution_plan

    manifest = load_manifest(MANIFEST_PATH)
    plan = build_execution_plan(manifest, repo_root=REPO_ROOT, include_live=True)

    assert plan.total_cases == 23
    assert plan.included_cases == 23
    assert plan.skipped_live_cases == 0
    assert any(
        "real-scoped-chat-records-reference-time-and-citations" in step.case_ids
        and step.cwd == REPO_ROOT / "apps" / "backend"
        and step.command.startswith("..\\..\\.venv\\Scripts\\python.exe -m app.verification.real_scope_chat_acceptance")
        for step in plan.steps
    )


def test_main_workflow_eval_runner_executes_plan_with_mocked_subprocess(tmp_path):
    from app.verification.main_workflow_eval_runner import run_manifest_commands

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_id": "test_manifest",
                "version": "test",
                "cases": [
                    {
                        "id": "det-a",
                        "priority": "P0",
                        "workflow": "chat",
                        "mode": "deterministic",
                        "coverage_status": "implemented",
                        "fixture": {"type": "unit"},
                        "command": "echo deterministic",
                        "acceptance_checks": ["a", "b"],
                        "evidence_artifacts": ["pytest"],
                    },
                    {
                        "id": "det-b-duplicate",
                        "priority": "P0",
                        "workflow": "chat",
                        "mode": "deterministic",
                        "coverage_status": "implemented",
                        "fixture": {"type": "unit"},
                        "command": "echo deterministic",
                        "acceptance_checks": ["a", "b"],
                        "evidence_artifacts": ["pytest"],
                    },
                    {
                        "id": "live-c",
                        "priority": "P1",
                        "workflow": "live_smoke",
                        "mode": "live_optional",
                        "coverage_status": "implemented",
                        "fixture": {"type": "live"},
                        "command": "echo live",
                        "acceptance_checks": ["a", "b"],
                        "evidence_artifacts": ["smoke"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def fake_run(args, **kwargs):
        calls.append({"args": args, "cwd": kwargs.get("cwd"), "env": kwargs.get("env")})
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    report = run_manifest_commands(
        manifest_path=manifest_path,
        repo_root=tmp_path,
        include_live=False,
        execute=True,
        require_valid_manifest=False,
        subprocess_run=fake_run,
    )

    assert report["status"] == "passed"
    assert report["included_cases"] == 2
    assert report["skipped_live_cases"] == 1
    assert report["unique_command_count"] == 1
    assert report["executed_command_count"] == 1
    assert report["commands"][0]["case_ids"] == ["det-a", "det-b-duplicate"]
    assert len(calls) == 1
    assert calls[0]["args"] == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "echo deterministic",
    ]
    assert calls[0]["cwd"] == str(tmp_path)
    assert str(tmp_path / "apps" / "backend") in calls[0]["env"]["PYTHONPATH"]
    assert calls[0]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert calls[0]["env"]["PYTEST_DATABASE_URL"].startswith("sqlite:///")


def test_main_workflow_eval_runner_report_includes_written_report_path(tmp_path):
    from app.verification.main_workflow_eval_runner import run_manifest_commands

    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "reports" / "runner-latest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_id": "test_manifest",
                "version": "test",
                "cases": [
                    {
                        "id": "det-a",
                        "priority": "P0",
                        "workflow": "chat",
                        "mode": "deterministic",
                        "coverage_status": "implemented",
                        "fixture": {"type": "unit"},
                        "command": "echo deterministic",
                        "acceptance_checks": ["a", "b"],
                        "evidence_artifacts": ["pytest"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_manifest_commands(
        manifest_path=manifest_path,
        repo_root=tmp_path,
        execute=False,
        require_valid_manifest=False,
        report_path=report_path,
    )

    assert report["report_path"] == str(report_path)
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["report_path"] == str(report_path)


def test_main_workflow_eval_runner_strips_ansi_from_command_output(tmp_path):
    from app.verification.main_workflow_eval_runner import run_manifest_commands

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_id": "test_manifest",
                "version": "test",
                "cases": [
                    {
                        "id": "det-a",
                        "priority": "P0",
                        "workflow": "chat",
                        "mode": "deterministic",
                        "coverage_status": "implemented",
                        "fixture": {"type": "unit"},
                        "command": "echo deterministic",
                        "acceptance_checks": ["a", "b"],
                        "evidence_artifacts": ["pytest"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_run(args, **kwargs):
        del args, kwargs
        return SimpleNamespace(returncode=0, stdout="\x1b[32mOK\x1b[0m", stderr="\x1b[31mWARN\x1b[0m")

    report = run_manifest_commands(
        manifest_path=manifest_path,
        repo_root=tmp_path,
        execute=True,
        require_valid_manifest=False,
        subprocess_run=fake_run,
    )

    command = report["commands"][0]
    assert command["stdout_tail"] == "OK"
    assert command["stderr_tail"] == "WARN"


def test_main_workflow_eval_runner_preflights_ui_services_before_execution(tmp_path):
    from app.verification.main_workflow_eval_runner import run_manifest_commands

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_id": "test_manifest",
                "version": "test",
                "cases": [
                    {
                        "id": "ui-acceptance",
                        "priority": "P0",
                        "workflow": "chat",
                        "mode": "deterministic",
                        "coverage_status": "implemented",
                        "fixture": {"type": "ui"},
                        "command": "npm.cmd run ui:acceptance",
                        "acceptance_checks": ["ui"],
                        "evidence_artifacts": ["playwright"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    subprocess_calls: list[dict] = []
    probed_urls: list[str] = []

    def fake_probe(url: str, timeout_seconds: float) -> bool:
        del timeout_seconds
        probed_urls.append(url)
        return url.endswith(":5173")

    def fake_run(args, **kwargs):
        subprocess_calls.append({"args": args, "cwd": kwargs.get("cwd")})
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    report = run_manifest_commands(
        manifest_path=manifest_path,
        repo_root=tmp_path,
        include_live=False,
        execute=True,
        require_valid_manifest=False,
        http_probe=fake_probe,
        subprocess_run=fake_run,
    )

    assert report["status"] == "failed"
    assert report["executed_command_count"] == 0
    assert subprocess_calls == []
    assert probed_urls == ["http://127.0.0.1:8000/health", "http://127.0.0.1:5173"]
    assert report["service_preflight"]["status"] == "failed"
    assert report["service_preflight"]["checks"][0] == {
        "name": "backend",
        "url": "http://127.0.0.1:8000/health",
        "ok": False,
    }
