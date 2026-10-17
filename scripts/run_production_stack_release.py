from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_production_operator_preflight import run_operator_preflight  # noqa: E402


OperatorPreflightRunner = Callable[..., dict[str, Any]]
CommandRunner = Callable[..., Any]
HealthChecker = Callable[..., dict[str, Any]]
DEFAULT_OUT_DIR = SCRIPT_DIR.parent / "docs" / "verification"
SECRET_SHAPE_PATTERNS = (
    (re.compile(r"sk-or-v1-[A-Za-z0-9_-]+"), "[redacted-openrouter-key]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@"), "[redacted-credential-uri]"),
    (re.compile(r"ENC\[AES256_GCM,"), "[redacted-sops-ciphertext]"),
)


def project_root_from_script() -> Path:
    return SCRIPT_DIR.parent


def _ensure_inside_project(path: Path, project_root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = project_root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Output directory must be inside project: {resolved_root}") from exc
    return resolved_path


def _safe_path_string(value: str, project_root: Path) -> str:
    normalized = value.replace("\\", "/")
    root = str(project_root.resolve()).replace("\\", "/")
    if normalized.startswith(root + "/"):
        return normalized[len(root) + 1 :]
    marker = "docs/verification/"
    if marker in normalized:
        return marker + normalized.split(marker, 1)[1]
    if "://" not in normalized and re.match(r"^[A-Za-z]:/[^\s]*$", normalized):
        return Path(normalized).name
    return value


def _redact_text(value: str, project_root: Path) -> str:
    text = _safe_path_string(value, project_root)
    root_native = str(project_root.resolve())
    root_normalized = root_native.replace("\\", "/")
    text = text.replace(root_native, "<project-root>")
    text = text.replace(root_normalized, "<project-root>")
    text = text.replace("\\", "/")
    for pattern, replacement in SECRET_SHAPE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _sanitize_export(value: Any, project_root: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_export(item, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_export(item, project_root) for item in value]
    if isinstance(value, str):
        return _redact_text(value, project_root)
    return value


def public_cli_result(result: dict[str, Any], *, project_root: Path | str | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    return _sanitize_export(result, root)


def format_cli_json(result: dict[str, Any]) -> str:
    return json.dumps(public_cli_result(result), ensure_ascii=True, indent=2)


def _write_report(
    report: dict[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    project_root: Path,
) -> dict[str, Any]:
    export_path = out_dir / f"production-stack-release-{stamp}.json"
    latest_path = out_dir / "production-stack-release-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return json.loads(text)


def _release_command_plan() -> list[str]:
    return [
        "docker compose --env-file <candidate-production-env> -f docker-compose.prod.yml build",
        "docker compose --env-file <candidate-production-env> -f docker-compose.prod.yml run --rm migrate",
        "docker compose --env-file <candidate-production-env> -f docker-compose.prod.yml up -d",
        "wait <backend-url>/health/ready",
        (
            "python scripts/run_production_operator_preflight.py --env-file <candidate-production-env> "
            "--include-live-gate --backend-url <backend-url> --fail-on-blocked"
        ),
    ]


def _docker_commands(env_path: Path, compose_path: Path) -> list[tuple[str, list[str]]]:
    base = ["docker", "compose", "--env-file", str(env_path), "-f", str(compose_path)]
    return [
        ("docker_build", [*base, "build"]),
        ("docker_migrate", [*base, "run", "--rm", "migrate"]),
        ("docker_up", [*base, "up", "-d"]),
    ]


def _step_from_result(key: str, label: str, result: dict[str, Any]) -> dict[str, Any]:
    status = str(result.get("status") or "unknown")
    exit_code = 0 if status == "pass" and result.get("release_candidate_ready", True) is not False else 2
    return {
        "key": key,
        "label": label,
        "status": status,
        "exit_code": exit_code,
        "export_path": result.get("export_path"),
        "latest_path": result.get("latest_path"),
        "blocking_steps": result.get("blocking_steps", []),
        "next_actions": result.get("next_actions", []),
    }


def _step_exception(key: str, label: str, exc: Exception) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": "fail",
        "exit_code": 2,
        "exception_type": type(exc).__name__,
        "next_actions": [
            {
                "key": f"{key}_execution",
                "label": label,
                "status": "fail",
                "category": "production_stack_release",
                "action": f"Fix {key} execution and rerun scripts/run_production_stack_release.py.",
                "owner": "release_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
            }
        ],
    }


def _command_step(key: str, label: str, command: Sequence[str], completed: Any) -> dict[str, Any]:
    returncode = int(getattr(completed, "returncode", 2))
    return {
        "key": key,
        "label": label,
        "status": "pass" if returncode == 0 else "fail",
        "exit_code": 0 if returncode == 0 else 2,
        "returncode": returncode,
        "command": list(command),
        "stdout": str(getattr(completed, "stdout", "") or "")[-4000:],
        "stderr": str(getattr(completed, "stderr", "") or "")[-4000:],
        "next_actions": []
        if returncode == 0
        else [
            {
                "key": key,
                "label": label,
                "status": "fail",
                "category": "docker_compose_release",
                "action": f"Inspect the {key} command output, fix the deployment issue, then rerun the stack release wrapper.",
                "owner": "release_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
            }
        ],
    }


def _aggregate_next_actions(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for step in steps:
        if step.get("exit_code") == 0:
            continue
        next_actions = step.get("next_actions") if isinstance(step.get("next_actions"), list) else []
        for action in next_actions:
            if not isinstance(action, dict):
                continue
            actions.append({"source_step": step.get("key"), **action})
    return actions


def _read_backend_readiness_summary(response: Any) -> dict[str, Any]:
    reader = getattr(response, "read", None)
    if not callable(reader):
        return {}
    try:
        raw = reader()
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}

    profile = payload.get("deployment_profile") if isinstance(payload.get("deployment_profile"), dict) else {}
    blocking_checks = profile.get("blocking_checks") if isinstance(profile.get("blocking_checks"), list) else []
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    worker = checks.get("ingestion_worker") if isinstance(checks.get("ingestion_worker"), dict) else {}
    try:
        normalized_worker_count = int(worker.get("worker_count") or 0)
    except (TypeError, ValueError):
        normalized_worker_count = 0

    summary: dict[str, Any] = {
        "runtime_status": str(payload.get("status") or "unknown"),
        "blocking_checks": [str(item) for item in blocking_checks],
    }
    if worker:
        summary["ingestion_worker"] = {
            "required": worker.get("required") is True,
            "mode": str(worker.get("mode") or "unknown"),
            "durable": worker.get("durable") is True,
            "worker_reachable": worker.get("worker_reachable"),
            "worker_count": normalized_worker_count,
        }
    return summary


def wait_for_backend_health(*, backend_url: str, timeout: float = 60.0, **_kwargs: Any) -> dict[str, Any]:
    ready_url = backend_url.rstrip("/") + "/health/ready"
    started = time.monotonic()
    deadline = started + timeout
    attempts = 0
    last_error = ""
    last_summary: dict[str, Any] = {}
    while time.monotonic() <= deadline:
        attempts += 1
        try:
            with urllib.request.urlopen(ready_url, timeout=min(2.0, max(0.5, timeout))) as response:
                status_code = int(response.getcode())
                last_summary = _read_backend_readiness_summary(response)
            if 200 <= status_code < 300:
                runtime_status = str(last_summary.get("runtime_status") or "")
                worker = last_summary.get("ingestion_worker")
                worker_unavailable = (
                    isinstance(worker, dict)
                    and worker.get("required") is True
                    and worker.get("worker_reachable") is not True
                )
                if runtime_status and runtime_status != "ready":
                    last_error = f"readiness status {runtime_status}"
                elif worker_unavailable:
                    last_error = "required ingestion worker is unavailable"
                else:
                    return {
                        "key": "backend_health",
                        "label": "Backend health",
                        "status": "pass",
                        "exit_code": 0,
                        "url": "<backend-url>/health/ready",
                        "attempts": attempts,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "next_actions": [],
                        **last_summary,
                    }
            else:
                last_error = f"HTTP {status_code}"
        except Exception as exc:  # pragma: no cover - exercised through injected checker in tests
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1)
    return {
        "key": "backend_health",
        "label": "Backend health",
        "status": "fail",
        "exit_code": 2,
        "url": "<backend-url>/health/ready",
        "attempts": attempts,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "last_error": last_error,
        **last_summary,
        "next_actions": [
            {
                "key": "backend_health",
                "label": "Backend health",
                "status": "fail",
                "category": "backend_runtime",
                "action": "Inspect backend container logs and readiness dependencies, then rerun the stack release wrapper.",
                "owner": "release_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
            }
        ],
    }


def run_stack_release(
    *,
    env_file: Path | str,
    backend_url: str = "http://127.0.0.1:8000",
    timeout: float = 60.0,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    execute: bool = False,
    compose_file: Path | str = "docker-compose.prod.yml",
    operator_preflight_runner: OperatorPreflightRunner = run_operator_preflight,
    command_runner: CommandRunner = subprocess.run,
    health_checker: HealthChecker = wait_for_backend_health,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    destination = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    if not destination.is_absolute():
        destination = root / destination
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    env_path = Path(env_file)
    if not env_path.is_absolute():
        env_path = root / env_path
    compose_path = Path(compose_file)
    if not compose_path.is_absolute():
        compose_path = root / compose_path

    steps: list[dict[str, Any]] = []
    execution_skipped_reason = ""

    try:
        static_result = operator_preflight_runner(
            env_file=env_path,
            backend_url=backend_url,
            timeout=timeout,
            out_dir=destination,
            project_root=root,
            now=timestamp,
            include_live_gate=False,
        )
        static_step = _step_from_result("static_operator_preflight", "Static operator preflight", static_result)
    except Exception as exc:
        static_step = _step_exception("static_operator_preflight", "Static operator preflight", exc)
    steps.append(static_step)

    static_passed = static_step["exit_code"] == 0
    if not static_passed:
        status = "blocked"
        execution_skipped_reason = "static_preflight_failed"
    elif not execute:
        status = "planned"
        execution_skipped_reason = "dry_run_plan_only"
    else:
        status = "pass"
        for key, command in _docker_commands(env_path, compose_path):
            try:
                completed = command_runner(command, cwd=str(root), text=True, capture_output=True, check=False)
                command_step = _command_step(key, key.replace("_", " ").title(), command, completed)
            except Exception as exc:
                command_step = _step_exception(key, key.replace("_", " ").title(), exc)
            steps.append(command_step)
            if command_step["exit_code"] != 0:
                status = "blocked"
                execution_skipped_reason = f"{key}_failed"
                break

        if status == "pass":
            try:
                health_result = health_checker(backend_url=backend_url, timeout=timeout, project_root=root)
                health_step = _step_from_result("backend_health", "Backend health", health_result)
                health_step.update({"url": health_result.get("url", "<backend-url>/health/ready")})
            except Exception as exc:
                health_step = _step_exception("backend_health", "Backend health", exc)
            steps.append(health_step)
            if health_step["exit_code"] != 0:
                status = "blocked"
                execution_skipped_reason = "backend_health_failed"

        if status == "pass":
            try:
                live_result = operator_preflight_runner(
                    env_file=env_path,
                    backend_url=backend_url,
                    timeout=timeout,
                    out_dir=destination,
                    project_root=root,
                    now=timestamp,
                    include_live_gate=True,
                )
                live_step = _step_from_result("live_operator_preflight", "Live operator preflight", live_result)
            except Exception as exc:
                live_step = _step_exception("live_operator_preflight", "Live operator preflight", exc)
            steps.append(live_step)
            if live_step["exit_code"] != 0:
                status = "blocked"
                execution_skipped_reason = "live_operator_preflight_failed"

    blocking_steps = [str(step["key"]) for step in steps if step.get("exit_code") != 0]
    next_actions = _aggregate_next_actions(steps)
    report = {
        "report_type": "production_stack_release",
        "status": status,
        "exit_code": 0 if status in {"pass", "planned"} else 2,
        "release_candidate_ready": status in {"pass", "planned"},
        "release_executed": any(str(step.get("key", "")).startswith("docker_") for step in steps),
        "execute_requested": execute,
        "execution_skipped_reason": execution_skipped_reason,
        "generated_at": timestamp.isoformat(),
        "env_file": str(env_path),
        "compose_file": str(compose_path),
        "backend_url": "<backend-url>",
        "blocking_steps": blocking_steps,
        "next_action_count": len(next_actions),
        "next_actions": next_actions,
        "release_command_plan": _release_command_plan(),
        "steps": steps,
        "policy_basis": [
            "Static production operator preflight must pass before any Docker operation.",
            "The default mode is planning only and has no container side effects.",
            "Docker build, migration, startup, backend readiness, and live release gate must pass in order.",
        ],
    }
    return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)


def stack_release_exit_code(result: dict[str, Any], *, fail_on_blocked: bool) -> int:
    if not fail_on_blocked:
        return 0
    return 0 if result.get("status") in {"pass", "planned"} else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan or execute the production Docker stack release with guarded preflight checks."
    )
    parser.add_argument("--env-file", default="apps/backend/.env.prod")
    parser.add_argument("--compose-file", default="docker-compose.prod.yml")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()

    result = run_stack_release(
        env_file=Path(args.env_file),
        compose_file=Path(args.compose_file),
        out_dir=Path(args.out_dir) if args.out_dir else None,
        backend_url=args.backend_url,
        timeout=args.timeout,
        execute=args.execute,
    )
    print(format_cli_json(result))
    return stack_release_exit_code(result, fail_on_blocked=args.fail_on_blocked)


if __name__ == "__main__":
    raise SystemExit(main())
