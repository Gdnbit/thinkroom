from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = SCRIPT_DIR.parent / "docs" / "verification"
SECRET_SHAPE_PATTERNS = (
    (re.compile(r"sk-or-v1-[A-Za-z0-9_-]+"), "[redacted-openrouter-key]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@"), "[redacted-credential-uri]"),
    (re.compile(r"ENC\[AES256_GCM,"), "[redacted-sops-ciphertext]"),
)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
FRONTEND_CHECKS = (
    {
        "key": "npm_audit",
        "label": "Frontend npm dependency audit",
        "args": ("run", "security:audit"),
        "env": {},
    },
    {
        "key": "eslint",
        "label": "Frontend ESLint",
        "args": ("run", "lint"),
        "env": {},
    },
    {
        "key": "typecheck",
        "label": "Frontend TypeScript type-check",
        "args": ("run", "type-check"),
        "env": {},
    },
    {
        "key": "vite_build",
        "label": "Frontend Vite production build",
        "args": ("run", "build"),
        "env": {"VITE_API_URL": "/api"},
    },
)
VERIFY_COMMAND = "python scripts/run_frontend_release_checks.py --fail-on-error"
CommandRunner = Callable[..., Any]


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
    if marker in normalized and not re.search(r"\s", normalized):
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
    export_path = out_dir / f"frontend-release-checks-{stamp}.json"
    latest_path = out_dir / "frontend-release-checks-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return json.loads(text)


def _tail(value: Any, limit: int = 4000) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    text = ANSI_ESCAPE_PATTERN.sub("", text)
    return text[-limit:]


def _default_npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _default_command_runner(command: Sequence[str], *, cwd: Path, env: dict[str, str], timeout: float) -> Any:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _check_failure_action() -> dict[str, Any]:
    return {
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
        "verification_commands": [VERIFY_COMMAND],
    }


def _run_check(
    spec: dict[str, Any],
    *,
    frontend_dir: Path,
    npm_executable: str,
    timeout: float,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    command = [npm_executable, *list(spec["args"])]
    env = dict(os.environ)
    env.update({str(key): str(value) for key, value in dict(spec.get("env") or {}).items()})
    started = time.perf_counter()
    try:
        completed = command_runner(command, cwd=frontend_dir, env=env, timeout=timeout)
        returncode = int(getattr(completed, "returncode", 2))
        stdout = _tail(getattr(completed, "stdout", ""))
        stderr = _tail(getattr(completed, "stderr", ""))
        exception_type = None
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = _tail(exc.stdout)
        stderr = _tail(exc.stderr or f"Command timed out after {timeout} seconds.")
        exception_type = type(exc).__name__
    except Exception as exc:
        returncode = 2
        stdout = ""
        stderr = str(exc)
        exception_type = type(exc).__name__
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    result = {
        "key": spec["key"],
        "label": spec["label"],
        "status": "pass" if returncode == 0 else "fail",
        "exit_code": 0 if returncode == 0 else 2,
        "returncode": returncode,
        "command": command,
        "cwd": str(frontend_dir),
        "duration_ms": duration_ms,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
    }
    if exception_type is not None:
        result["exception_type"] = exception_type
    return result


def _is_transient_audit_endpoint_failure(result: dict[str, Any]) -> bool:
    if result.get("key") != "npm_audit" or result.get("returncode") == 0:
        return False
    rendered = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}".lower()
    transient_signals = (
        "audit endpoint returned an error",
        "endpoint is being retired",
        "npm warn audit 400",
        "npm warn audit 429",
        "npm warn audit 500",
        "npm warn audit 502",
        "npm warn audit 503",
        "npm warn audit 504",
        "econnreset",
        "econnrefused",
        "etimedout",
        "fetch failed",
    )
    return any(signal in rendered for signal in transient_signals)


def _run_check_with_retries(
    spec: dict[str, Any],
    *,
    frontend_dir: Path,
    npm_executable: str,
    timeout: float,
    command_runner: CommandRunner,
    audit_retry_count: int,
    audit_retry_delay_seconds: float,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    total_attempts = 1 + (max(0, int(audit_retry_count)) if spec.get("key") == "npm_audit" else 0)
    for attempt_index in range(1, total_attempts + 1):
        result = _run_check(
            spec,
            frontend_dir=frontend_dir,
            npm_executable=npm_executable,
            timeout=timeout,
            command_runner=command_runner,
        )
        attempts.append(
            {
                "attempt": attempt_index,
                "status": result.get("status"),
                "returncode": result.get("returncode"),
                "duration_ms": result.get("duration_ms"),
                "stdout_tail": result.get("stdout_tail"),
                "stderr_tail": result.get("stderr_tail"),
            }
        )
        transient = _is_transient_audit_endpoint_failure(result)
        if result.get("returncode") == 0 or not transient or attempt_index >= total_attempts:
            result["attempt_count"] = len(attempts)
            result["recovered_transient_failure"] = bool(result.get("returncode") == 0 and len(attempts) > 1)
            result["attempts"] = attempts
            return result
        if audit_retry_delay_seconds > 0:
            time.sleep(audit_retry_delay_seconds)
    raise AssertionError("unreachable audit retry state")


def run_frontend_release_checks(
    *,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    timeout: float = 120.0,
    command_runner: CommandRunner = _default_command_runner,
    npm_executable: str | None = None,
    audit_retry_count: int = 2,
    audit_retry_delay_seconds: float = 1.0,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    destination = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    if not destination.is_absolute():
        destination = root / destination
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    frontend_dir = root / "apps" / "frontend"
    npm = npm_executable or _default_npm_executable()

    if not frontend_dir.exists():
        report = {
            "report_type": "frontend_release_checks",
            "status": "fail",
            "release_allowed": False,
            "generated_at": timestamp.isoformat(),
            "frontend_dir": str(frontend_dir),
            "command_count": 0,
            "failed_command_count": 1,
            "failed_commands": ["frontend_dir"],
            "checks": [],
            "next_actions": [_check_failure_action()],
            "policy_basis": [
                "Frontend release readiness must prove npm audit, lint, type-check, and production build on the current tree.",
            ],
        }
        return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)

    checks = [
        _run_check_with_retries(
            dict(spec),
            frontend_dir=frontend_dir,
            npm_executable=npm,
            timeout=timeout,
            command_runner=command_runner,
            audit_retry_count=audit_retry_count,
            audit_retry_delay_seconds=audit_retry_delay_seconds,
        )
        for spec in FRONTEND_CHECKS
    ]
    failed_commands = [str(check["key"]) for check in checks if check.get("exit_code") != 0]
    status = "pass" if not failed_commands else "fail"
    report = {
        "report_type": "frontend_release_checks",
        "status": status,
        "release_allowed": status == "pass",
        "generated_at": timestamp.isoformat(),
        "frontend_dir": str(frontend_dir),
        "command_count": len(checks),
        "failed_command_count": len(failed_commands),
        "failed_commands": failed_commands,
        "checks": checks,
        "next_actions": [] if status == "pass" else [_check_failure_action()],
        "policy_basis": [
            "Frontend release readiness must prove npm audit, lint, type-check, and production build on the current tree.",
            "The release summary consumes this latest artifact instead of relying only on CI memory.",
            "Known npm registry audit endpoint failures are retried a bounded number of times; vulnerability results are never converted to pass.",
        ],
    }
    return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)


def frontend_release_checks_exit_code(result: dict[str, Any], *, fail_on_error: bool) -> int:
    if not fail_on_error:
        return 0
    return 0 if result.get("status") == "pass" and result.get("release_allowed") is True else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frontend release checks and write a production readiness artifact.")
    parser.add_argument("--out-dir", default="docs/verification")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--npm-executable", default=None)
    parser.add_argument("--audit-retries", type=int, default=2)
    parser.add_argument("--audit-retry-delay", type=float, default=1.0)
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    result = run_frontend_release_checks(
        out_dir=Path(args.out_dir),
        timeout=args.timeout,
        npm_executable=args.npm_executable,
        audit_retry_count=args.audit_retries,
        audit_retry_delay_seconds=args.audit_retry_delay,
    )
    print(format_cli_json(result))
    return frontend_release_checks_exit_code(result, fail_on_error=args.fail_on_error)


if __name__ == "__main__":
    raise SystemExit(main())
