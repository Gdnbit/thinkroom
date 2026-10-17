from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_production_compose_config import check_compose_config  # noqa: E402
from run_release_gate import run_release_gate  # noqa: E402
from validate_production_env import validate_env_file, validation_exit_code  # noqa: E402


EnvValidator = Callable[..., dict[str, Any]]
ComposeChecker = Callable[..., dict[str, Any]]
LiveGateRunner = Callable[..., dict[str, Any]]
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
    if ":/" in normalized:
        return Path(normalized).name
    return value


def _redact_text(value: str, project_root: Path) -> str:
    text = _safe_path_string(value, project_root)
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


def _step(key: str, label: str, result: dict[str, Any], *, exit_code: int) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": result.get("status") or "unknown",
        "exit_code": exit_code,
        "export_path": result.get("export_path"),
        "latest_path": result.get("latest_path"),
        "next_actions": result.get("next_actions", []),
    }


def _step_exception(key: str, label: str, exc: Exception) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": "fail",
        "exception_type": type(exc).__name__,
        "next_actions": [
            {
                "key": f"{key}_execution",
                "label": label,
                "status": "fail",
                "category": "operator_preflight_execution",
                "action": f"Fix {key} execution and rerun scripts/run_production_operator_preflight.py.",
                "owner": "release_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
            }
        ],
    }


def _exit_for_status(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == "pass" else 2


def _live_gate_exit(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == "pass" and result.get("release_allowed") is True else 2


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


def _operator_command_plan(include_live_gate: bool) -> list[str]:
    commands = [
        "python scripts/validate_production_env.py --env-file <candidate-production-env> --fail-on-error",
        "python scripts/check_production_compose_config.py --env-file <candidate-production-env> --fail-on-error",
    ]
    if include_live_gate:
        commands.append(
            "python scripts/run_release_gate.py --require-env-file --env-file <candidate-production-env> --backend-url <backend-url>"
        )
    else:
        commands.append(
            "python scripts/run_production_operator_preflight.py --env-file <candidate-production-env> --include-live-gate --backend-url <backend-url>"
        )
    return commands


def _write_report(
    report: dict[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    project_root: Path,
) -> dict[str, Any]:
    export_path = out_dir / f"production-operator-preflight-{stamp}.json"
    latest_path = out_dir / "production-operator-preflight-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return json.loads(text)


def run_operator_preflight(
    *,
    env_file: Path | str,
    backend_url: str = "http://127.0.0.1:8000",
    timeout: float = 20.0,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    include_live_gate: bool = False,
    env_validator: EnvValidator = validate_env_file,
    compose_checker: ComposeChecker = check_compose_config,
    live_gate_runner: LiveGateRunner = run_release_gate,
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

    steps: list[dict[str, Any]] = []

    try:
        env_result = env_validator(env_path, out_dir=destination, project_root=root, now=timestamp)
        env_exit = validation_exit_code(env_result, fail_on_error=True)
    except Exception as exc:
        env_result = _step_exception("production_env_validation", "Production env validation", exc)
        env_exit = 2
    steps.append(_step("production_env_validation", "Production env validation", env_result, exit_code=env_exit))

    try:
        compose_result = compose_checker(env_file=env_path, out_dir=destination, project_root=root, now=timestamp)
        compose_exit = _exit_for_status(compose_result)
    except Exception as exc:
        compose_result = _step_exception("production_compose_config", "Production compose config", exc)
        compose_exit = 2
    steps.append(_step("production_compose_config", "Production compose config", compose_result, exit_code=compose_exit))

    live_gate_included = False
    live_gate_skipped_reason = ""
    static_passed = all(step["exit_code"] == 0 for step in steps)
    if include_live_gate and static_passed:
        try:
            live_result = live_gate_runner(
                backend_url=backend_url,
                timeout=timeout,
                out_dir=destination,
                project_root=root,
                now=timestamp,
                env_file=env_path,
                require_env_file=True,
            )
            live_exit = _live_gate_exit(live_result)
        except Exception as exc:
            live_result = _step_exception("release_gate", "Release gate", exc)
            live_exit = 2
        live_gate_included = True
        steps.append(_step("release_gate", "Release gate", live_result, exit_code=live_exit))
    elif include_live_gate:
        live_gate_skipped_reason = "static_checks_failed"

    blocking_steps = [str(step["key"]) for step in steps if step["exit_code"] != 0]
    next_actions = _aggregate_next_actions(steps)
    status = "pass" if not blocking_steps else "blocked"
    report = {
        "report_type": "production_operator_preflight",
        "status": status,
        "exit_code": 0 if status == "pass" else 2,
        "release_candidate_ready": status == "pass",
        "generated_at": timestamp.isoformat(),
        "env_file": str(env_path),
        "backend_url": "<backend-url>" if include_live_gate else "",
        "live_gate_requested": include_live_gate,
        "live_gate_included": live_gate_included,
        "live_gate_skipped_reason": live_gate_skipped_reason,
        "blocking_steps": blocking_steps,
        "next_action_count": len(next_actions),
        "next_actions": next_actions,
        "operator_command_plan": _operator_command_plan(include_live_gate),
        "steps": steps,
        "policy_basis": [
            "Production env validation must pass before deployment.",
            "Docker Compose production config must render before deployment.",
            "Live release gate should be run explicitly after static checks and backend startup.",
        ],
    }
    return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)


def preflight_exit_code(result: dict[str, Any], *, fail_on_blocked: bool) -> int:
    if not fail_on_blocked:
        return 0
    return 0 if result.get("status") == "pass" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run safe production operator preflight checks without printing secrets.")
    parser.add_argument("--env-file", default="apps/backend/.env.prod")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--include-live-gate", action="store_true")
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()

    result = run_operator_preflight(
        env_file=Path(args.env_file),
        out_dir=Path(args.out_dir) if args.out_dir else None,
        backend_url=args.backend_url,
        timeout=args.timeout,
        include_live_gate=args.include_live_gate,
    )
    print(format_cli_json(result))
    return preflight_exit_code(result, fail_on_blocked=args.fail_on_blocked)


if __name__ == "__main__":
    raise SystemExit(main())
