from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "run_frontend_release_checks.py"
    spec = importlib.util.spec_from_file_location("run_frontend_release_checks", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontend_release_checks_writes_release_evidence_for_all_required_commands(tmp_path):
    module = _load_module()
    frontend_dir = tmp_path / "apps" / "frontend"
    frontend_dir.mkdir(parents=True)
    calls: list[tuple[list[str], Path, str | None]] = []

    def fake_runner(command, *, cwd, env, timeout):
        calls.append((list(command), cwd, env.get("VITE_API_URL"), timeout))
        return subprocess.CompletedProcess(command, 0, stdout=f"{command[-1]} ok\n", stderr="")

    result = module.run_frontend_release_checks(
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 8, 10, tzinfo=timezone.utc),
        command_runner=fake_runner,
        npm_executable="npm.cmd",
    )

    assert result["report_type"] == "frontend_release_checks"
    assert result["status"] == "pass"
    assert result["release_allowed"] is True
    assert result["frontend_dir"] == "apps/frontend"
    assert result["failed_command_count"] == 0
    assert [check["key"] for check in result["checks"]] == [
        "npm_audit",
        "eslint",
        "typecheck",
        "vite_build",
    ]
    assert [call[0] for call in calls] == [
        ["npm.cmd", "run", "security:audit"],
        ["npm.cmd", "run", "lint"],
        ["npm.cmd", "run", "type-check"],
        ["npm.cmd", "run", "build"],
    ]
    assert calls[-1][2] == "/api"
    assert result["latest_path"] == "docs/verification/frontend-release-checks-latest.json"
    latest = tmp_path / result["latest_path"]
    assert latest.exists()
    persisted = json.loads(latest.read_text(encoding="utf-8"))
    assert persisted["status"] == "pass"
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)


def test_frontend_release_checks_blocks_and_points_to_single_rerun_command(tmp_path):
    module = _load_module()
    frontend_dir = tmp_path / "apps" / "frontend"
    frontend_dir.mkdir(parents=True)

    def fake_runner(command, *, cwd, env, timeout):
        if command[-1] == "lint":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="lint failed")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = module.run_frontend_release_checks(
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 8, 20, tzinfo=timezone.utc),
        command_runner=fake_runner,
        npm_executable="npm.cmd",
    )

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert result["failed_commands"] == ["eslint"]
    assert result["next_actions"] == [
        {
            "key": "frontend_release_checks",
            "label": "Frontend release checks",
            "status": "fail",
            "category": "release_verification",
            "action": "Fix frontend npm audit, lint, type-check, and build failures, then rerun the frontend release checks.",
            "owner": "frontend",
            "can_auto_fix": False,
            "requires_secret_update": False,
            "production_required": True,
            "config_keys": [],
            "verification_commands": ["python scripts/run_frontend_release_checks.py --fail-on-error"],
        }
    ]
    lint_check = next(check for check in result["checks"] if check["key"] == "eslint")
    assert lint_check["status"] == "fail"
    assert lint_check["stderr_tail"] == "lint failed"


def test_default_frontend_command_runner_uses_utf8_replacement_for_windows_output(monkeypatch, tmp_path):
    module = _load_module()
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._default_command_runner(["npm.cmd", "run", "build"], cwd=tmp_path, env={}, timeout=1)

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["text"] is True


def test_frontend_command_output_tail_strips_ansi_control_sequences():
    module = _load_module()

    assert module._tail("\x1b[32mbuilt in 12.47s\x1b[39m") == "built in 12.47s"


def test_frontend_release_checks_retries_only_transient_npm_audit_endpoint_failures(tmp_path):
    module = _load_module()
    frontend_dir = tmp_path / "apps" / "frontend"
    frontend_dir.mkdir(parents=True)
    audit_attempts = 0

    def fake_runner(command, *, cwd, env, timeout):
        nonlocal audit_attempts
        if command[-1] == "security:audit":
            audit_attempts += 1
            if audit_attempts == 1:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="statusCode: 400",
                    stderr="npm warn audit 400 Bad Request\nnpm error audit endpoint returned an error",
                )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = module.run_frontend_release_checks(
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 10, 20, 30, tzinfo=timezone.utc),
        command_runner=fake_runner,
        npm_executable="npm.cmd",
        audit_retry_count=2,
        audit_retry_delay_seconds=0,
    )

    audit = next(check for check in result["checks"] if check["key"] == "npm_audit")
    assert result["status"] == "pass"
    assert audit_attempts == 2
    assert audit["attempt_count"] == 2
    assert audit["recovered_transient_failure"] is True
    assert [attempt["status"] for attempt in audit["attempts"]] == ["fail", "pass"]


def test_frontend_release_checks_does_not_retry_a_real_vulnerability_result(tmp_path):
    module = _load_module()
    frontend_dir = tmp_path / "apps" / "frontend"
    frontend_dir.mkdir(parents=True)
    audit_attempts = 0

    def fake_runner(command, *, cwd, env, timeout):
        nonlocal audit_attempts
        if command[-1] == "security:audit":
            audit_attempts += 1
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="1 high severity vulnerability",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = module.run_frontend_release_checks(
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 10, 20, 31, tzinfo=timezone.utc),
        command_runner=fake_runner,
        npm_executable="npm.cmd",
        audit_retry_count=2,
        audit_retry_delay_seconds=0,
    )

    audit = next(check for check in result["checks"] if check["key"] == "npm_audit")
    assert result["status"] == "fail"
    assert audit_attempts == 1
    assert audit["attempt_count"] == 1
    assert audit["recovered_transient_failure"] is False
