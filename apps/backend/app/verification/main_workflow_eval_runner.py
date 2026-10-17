"""Executable runner for the main workflow evaluation manifest.

The manifest validator checks that the production-quality contract is well
formed. This module turns the implemented cases into a deduplicated command
plan and can execute it when explicitly requested.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.verification.main_workflow_eval_manifest import default_manifest_path, load_manifest, validate_manifest


@dataclass(frozen=True)
class ManifestCommandStep:
    command: str
    cwd: Path
    mode: str
    workflow: str
    case_ids: list[str]
    env: dict[str, str]
    service_dependencies: list[dict[str, str]]


@dataclass(frozen=True)
class ManifestExecutionPlan:
    manifest_id: str | None
    version: str | None
    total_cases: int
    included_cases: int
    skipped_live_cases: int
    unique_command_count: int
    steps: list[ManifestCommandStep]


SubprocessRun = Callable[..., Any]
HttpProbe = Callable[[str, float], bool]


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _normalize_command(command: str, repo_root: Path) -> tuple[str, Path]:
    normalized = " ".join(command.strip().split())
    frontend_prefix = "cd apps\\frontend; "
    if normalized.lower().startswith(frontend_prefix.lower()):
        normalized = normalized[len(frontend_prefix):].strip()
        return normalized, repo_root / "apps" / "frontend"
    if "npm.cmd run ui:acceptance" in normalized or "npm.cmd run build" in normalized:
        return normalized, repo_root / "apps" / "frontend"
    if " -m app.verification." in normalized:
        normalized = normalized.replace(
            ".\\.venv\\Scripts\\python.exe",
            "..\\..\\.venv\\Scripts\\python.exe",
            1,
        )
        return normalized, repo_root / "apps" / "backend"
    return normalized, repo_root


def _command_env(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    backend_path = str(repo_root / "apps" / "backend")
    runner_db_dir = repo_root / ".runtime_test_dbs"
    runner_db_dir.mkdir(parents=True, exist_ok=True)
    runner_db_url = f"sqlite:///{(runner_db_dir / f'main_workflow_eval_{os.getpid()}.db').as_posix()}"
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        backend_path if not current_pythonpath else f"{backend_path}{os.pathsep}{current_pythonpath}"
    )
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTEST_DATABASE_URL"] = runner_db_url
    return env


def _service_dependencies_for_command(command: str, env: dict[str, str]) -> list[dict[str, str]]:
    if "npm.cmd run ui:acceptance" not in command:
        return []
    return [
        {
            "name": "backend",
            "url": env.get("UI_ACCEPTANCE_BACKEND_HEALTH_URL", "http://127.0.0.1:8000/health"),
        },
        {
            "name": "frontend",
            "url": env.get("UI_ACCEPTANCE_BASE_URL", "http://127.0.0.1:5173"),
        },
    ]


def build_execution_plan(
    manifest: dict[str, Any],
    *,
    repo_root: str | Path | None = None,
    include_live: bool = False,
) -> ManifestExecutionPlan:
    root = Path(repo_root) if repo_root else default_repo_root()
    cases = manifest.get("cases") if isinstance(manifest.get("cases"), list) else []
    env = _command_env(root)
    steps_by_key: dict[tuple[str, str], ManifestCommandStep] = {}
    included_cases = 0
    skipped_live_cases = 0

    for case in cases:
        if not isinstance(case, dict):
            continue
        if case.get("coverage_status") != "implemented":
            continue
        mode = str(case.get("mode") or "")
        if mode == "live_optional" and not include_live:
            skipped_live_cases += 1
            continue
        command = str(case.get("command") or "").strip()
        case_id = str(case.get("id") or "").strip()
        if not command or not case_id:
            continue
        normalized_command, cwd = _normalize_command(command, root)
        key = (str(cwd), normalized_command)
        included_cases += 1
        existing = steps_by_key.get(key)
        if existing:
            steps_by_key[key] = ManifestCommandStep(
                command=existing.command,
                cwd=existing.cwd,
                mode=existing.mode,
                workflow=existing.workflow,
                case_ids=[*existing.case_ids, case_id],
                env=existing.env,
                service_dependencies=existing.service_dependencies,
            )
        else:
            steps_by_key[key] = ManifestCommandStep(
                command=normalized_command,
                cwd=cwd,
                mode=mode,
                workflow=str(case.get("workflow") or ""),
                case_ids=[case_id],
                env=env,
                service_dependencies=_service_dependencies_for_command(normalized_command, env),
            )

    steps = list(steps_by_key.values())
    return ManifestExecutionPlan(
        manifest_id=manifest.get("manifest_id") if isinstance(manifest.get("manifest_id"), str) else None,
        version=manifest.get("version") if isinstance(manifest.get("version"), str) else None,
        total_cases=len(cases),
        included_cases=included_cases,
        skipped_live_cases=skipped_live_cases,
        unique_command_count=len(steps),
        steps=steps,
    )


def _step_to_report(step: ManifestCommandStep, *, result: Any | None = None, status: str = "planned") -> dict[str, Any]:
    item = {
        "status": status,
        "command": step.command,
        "cwd": str(step.cwd),
        "mode": step.mode,
        "workflow": step.workflow,
        "case_ids": step.case_ids,
        "service_dependencies": step.service_dependencies,
        "env": {
            "PYTHONPATH": step.env.get("PYTHONPATH"),
            "PYTHONIOENCODING": step.env.get("PYTHONIOENCODING"),
            "PYTEST_DATABASE_URL": step.env.get("PYTEST_DATABASE_URL"),
        },
    }
    if result is not None:
        item["returncode"] = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if stdout:
            item["stdout_tail"] = ANSI_ESCAPE_RE.sub("", str(stdout))[-4000:]
        if stderr:
            item["stderr_tail"] = ANSI_ESCAPE_RE.sub("", str(stderr))[-4000:]
    return item


def default_http_probe(url: str, timeout_seconds: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return 200 <= int(getattr(response, "status", 0)) < 500
    except Exception:
        return False


def _preflight_services(
    steps: list[ManifestCommandStep],
    *,
    http_probe: HttpProbe,
    timeout_seconds: float,
) -> dict[str, Any]:
    unique_dependencies: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        for dependency in step.service_dependencies:
            name = dependency.get("name", "")
            url = dependency.get("url", "")
            key = (name, url)
            if not name or not url or key in seen:
                continue
            seen.add(key)
            unique_dependencies.append({"name": name, "url": url})

    checks = [
        {
            "name": dependency["name"],
            "url": dependency["url"],
            "ok": bool(http_probe(dependency["url"], timeout_seconds)),
        }
        for dependency in unique_dependencies
    ]
    return {
        "status": "passed" if all(check["ok"] for check in checks) else "failed",
        "checks": checks,
    }


def run_manifest_commands(
    *,
    manifest_path: str | Path | None = None,
    repo_root: str | Path | None = None,
    include_live: bool = False,
    execute: bool = False,
    require_valid_manifest: bool = True,
    report_path: str | Path | None = None,
    subprocess_run: SubprocessRun = subprocess.run,
    preflight_services: bool = True,
    service_timeout_seconds: float = 2.0,
    http_probe: HttpProbe = default_http_probe,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root else default_repo_root()
    resolved_manifest_path = Path(manifest_path) if manifest_path else default_manifest_path()
    manifest = load_manifest(resolved_manifest_path)
    validation_report = validate_manifest(manifest)
    plan = build_execution_plan(manifest, repo_root=root, include_live=include_live)

    commands: list[dict[str, Any]] = []
    failed_commands = 0
    executed_commands = 0
    service_preflight = {"status": "skipped", "checks": []}
    if require_valid_manifest and validation_report["status"] != "passed":
        status = "failed"
        commands = [_step_to_report(step) for step in plan.steps]
    elif not execute:
        status = "planned"
        commands = [_step_to_report(step) for step in plan.steps]
    else:
        if preflight_services:
            service_preflight = _preflight_services(
                plan.steps,
                http_probe=http_probe,
                timeout_seconds=service_timeout_seconds,
            )
        if service_preflight["status"] == "failed":
            status = "failed"
            commands = [_step_to_report(step) for step in plan.steps]
        else:
            status = "passed"
            for step in plan.steps:
                result = subprocess_run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", step.command],
                    cwd=str(step.cwd),
                    env=step.env,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                )
                executed_commands += 1
                step_status = "passed" if getattr(result, "returncode", 1) == 0 else "failed"
                if step_status == "failed":
                    failed_commands += 1
                    status = "failed"
                commands.append(_step_to_report(step, result=result, status=step_status))
                if step_status == "failed":
                    break

    report = {
        "report_type": "main_workflow_eval_runner",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(resolved_manifest_path),
        "manifest_status": validation_report["status"],
        "manifest_errors": validation_report["errors"],
        "manifest_id": plan.manifest_id,
        "version": plan.version,
        "total_cases": plan.total_cases,
        "included_cases": plan.included_cases,
        "skipped_live_cases": plan.skipped_live_cases,
        "unique_command_count": plan.unique_command_count,
        "execute": execute,
        "include_live": include_live,
        "require_valid_manifest": require_valid_manifest,
        "preflight_services": preflight_services,
        "service_preflight": service_preflight,
        "executed_command_count": executed_commands,
        "failed_command_count": failed_commands,
        "commands": commands,
    }
    if report_path:
        resolved_report_path = Path(report_path)
        resolved_report_path.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(resolved_report_path)
        resolved_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Plan or execute main workflow manifest commands.")
    parser.add_argument("--manifest", default=None, help="Path to main_workflow_eval_manifest.json")
    parser.add_argument("--repo-root", default=None, help="Repository root. Defaults to project root.")
    parser.add_argument("--include-live", action="store_true", help="Include live_optional cases.")
    parser.add_argument("--execute", action="store_true", help="Execute commands. Defaults to dry-run planning.")
    parser.add_argument("--report", default=None, help="Optional report JSON path.")
    parser.add_argument(
        "--no-service-preflight",
        action="store_true",
        help="Skip HTTP reachability checks for commands with service dependencies.",
    )
    parser.add_argument("--service-timeout", type=float, default=2.0, help="HTTP preflight timeout in seconds.")
    args = parser.parse_args(argv)

    root = Path(args.repo_root) if args.repo_root else default_repo_root()
    report_path = (
        Path(args.report)
        if args.report
        else root / "docs" / "verification" / "main_workflow_eval_runner_latest.json"
    )
    report = run_manifest_commands(
        manifest_path=args.manifest,
        repo_root=root,
        include_live=args.include_live,
        execute=args.execute,
        report_path=report_path,
        preflight_services=not args.no_service_preflight,
        service_timeout_seconds=args.service_timeout,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"passed", "planned"} else 1


if __name__ == "__main__":
    sys.exit(main())
