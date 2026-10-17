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
from check_verification_reports import (  # noqa: E402
    check_verification_reports,
    exit_code as report_hygiene_exit_code,
)
from run_frontend_release_checks import run_frontend_release_checks  # noqa: E402
from run_production_operator_preflight import run_operator_preflight  # noqa: E402
from run_production_stack_release import run_stack_release  # noqa: E402
from run_release_gate import run_release_gate  # noqa: E402
from summarize_production_readiness import summarize_production_readiness  # noqa: E402
from validate_secret_inventory import validate_secret_inventory  # noqa: E402


ReleaseGateRunner = Callable[..., dict[str, Any]]
OperatorPreflightRunner = Callable[..., dict[str, Any]]
StackReleaseRunner = Callable[..., dict[str, Any]]
FrontendReleaseChecksRunner = Callable[..., dict[str, Any]]
SummaryRunner = Callable[..., dict[str, Any]]
ReportHygieneChecker = Callable[..., dict[str, Any]]

EXPECTED_PRODUCTION_ENV_FILE = "apps/backend/.env.prod"
MATERIALIZE_PRODUCTION_ENV_DRY_RUN_COMMAND = (
    "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error"
)
MATERIALIZE_PRODUCTION_ENV_WRITE_COMMAND = (
    "python scripts/materialize_production_env.py --overwrite --source-label sops:production --fail-on-error"
)
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
    path_like = (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:/", normalized) is not None
        or normalized.startswith("docs/verification/")
        or normalized.startswith("apps/backend/")
    )
    if not path_like:
        return value
    root = str(project_root.resolve()).replace("\\", "/")
    if normalized.startswith(root + "/"):
        return normalized[len(root) + 1 :]
    for marker in ("docs/verification/", "apps/backend/"):
        if marker in normalized:
            return marker + normalized.split(marker, 1)[1]
    if "://" not in normalized and re.match(r"^[A-Za-z]:/[^\s]*$", normalized):
        return Path(normalized).name
    return value


def _redact_text(value: str, project_root: Path) -> str:
    text = _safe_path_string(value, project_root).replace("\\", "/")
    root_native = str(project_root.resolve())
    root_normalized = root_native.replace("\\", "/")
    text = text.replace(root_native, "<project-root>")
    text = text.replace(root_normalized, "<project-root>")
    text = re.sub(
        r"\bhttps?://(?:127\.0\.0\.1|localhost)(?::\d+)?\b",
        "[backend-url]",
        text,
        flags=re.IGNORECASE,
    )
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
    export_path = out_dir / f"production-readiness-refresh-{stamp}.json"
    latest_path = out_dir / "production-readiness-refresh-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return json.loads(text)


def _write_hygiene_report(
    report: dict[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    project_root: Path,
) -> dict[str, Any]:
    export_path = out_dir / f"verification-report-hygiene-{stamp}.json"
    latest_path = out_dir / "verification-report-hygiene-latest.json"
    artifact = {
        **report,
        "export_path": str(export_path),
        "latest_path": str(latest_path),
    }
    text = json.dumps(public_cli_result(artifact, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return json.loads(text)


def _relative_candidate_env(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return _safe_path_string(str(path), project_root)


def _candidate_env_guard(env_path: Path, project_root: Path) -> dict[str, str] | None:
    actual = _relative_candidate_env(env_path, project_root)
    if actual == EXPECTED_PRODUCTION_ENV_FILE:
        return None
    return {
        "expected_env_file": EXPECTED_PRODUCTION_ENV_FILE,
        "actual_env_file": actual,
    }


def _candidate_env_action(mismatch: dict[str, str]) -> dict[str, Any]:
    return {
        "key": "candidate_env_file",
        "label": "Candidate production env file",
        "status": "fail",
        "category": "production_readiness_refresh",
        "action": (
            f"Do not refresh production readiness evidence from {mismatch['actual_env_file']}; "
            f"create or inject the approved {mismatch['expected_env_file']} candidate and rerun "
            "scripts/run_production_readiness_refresh.py."
        ),
        "owner": "release_operator",
        "can_auto_fix": False,
        "requires_secret_update": True,
        "production_required": True,
        "config_keys": [],
        "verification_commands": [
            MATERIALIZE_PRODUCTION_ENV_DRY_RUN_COMMAND,
            MATERIALIZE_PRODUCTION_ENV_WRITE_COMMAND,
            "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked",
        ],
    }


def _step(key: str, label: str, result: dict[str, Any], *, exit_code: int) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": result.get("status") or "unknown",
        "exit_code": exit_code,
        "latest_path": result.get("latest_path") or result.get("digest_latest_path"),
        "blocking_steps": result.get("blocking_steps", []),
        "next_actions": result.get("next_actions", result.get("required_actions", [])),
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
                "category": "production_readiness_refresh",
                "action": f"Fix {key} execution and rerun scripts/run_production_readiness_refresh.py.",
                "owner": "release_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
            }
        ],
    }


def _release_gate_exit(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == "pass" and result.get("release_allowed") is True else 2


def _operator_exit(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == "pass" and result.get("release_candidate_ready") is True else 2


def _stack_exit(result: dict[str, Any]) -> int:
    return 0 if result.get("status") in {"planned", "pass"} and result.get("release_candidate_ready") is True else 2


def _frontend_exit(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == "pass" and result.get("release_allowed") is True else 2


def _summary_exit(result: dict[str, Any]) -> int:
    return 0 if result.get("status") == "pass" and result.get("release_allowed") is True else 2


def _refresh_command_plan() -> list[str]:
    return [
        "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod",
        "python scripts/run_frontend_release_checks.py --fail-on-error",
        "python scripts/summarize_production_readiness.py --report-dir docs/verification --fail-on-blocked",
        "python scripts/check_verification_reports.py docs/verification --fail-on-issue",
    ]


def _append_unique(values: list[str], value: Any) -> None:
    if value is None:
        return
    item = str(value)
    if item and item not in values:
        values.append(item)


def _sorted_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if str(item).strip()})


def _action_config_signature_keys(action: dict[str, Any]) -> list[str]:
    split_from_keys = _sorted_string_list(action.get("split_from_config_keys"))
    if split_from_keys:
        return split_from_keys
    return _sorted_string_list(action.get("config_keys"))


def _action_signature(action: dict[str, Any]) -> str:
    config_keys = _action_config_signature_keys(action)
    comparable = {
        "key": action.get("key"),
        "status": action.get("status"),
        "owner": action.get("owner"),
        "can_auto_fix": action.get("can_auto_fix"),
        "requires_secret_update": action.get("requires_secret_update"),
        "production_required": action.get("production_required"),
        "config_keys": config_keys,
    }
    if not config_keys:
        comparable["action"] = action.get("action")
    return json.dumps(comparable, ensure_ascii=True, sort_keys=True)


def _append_unique_json(values: list[Any], value: Any) -> None:
    signature = json.dumps(value, ensure_ascii=True, sort_keys=True)
    for existing in values:
        if json.dumps(existing, ensure_ascii=True, sort_keys=True) == signature:
            return
    values.append(value)


def _merge_action_metadata(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("label", "category", "reason"):
        if not target.get(key) and source.get(key):
            target[key] = source[key]
    for key in ("config_keys", "alternative_config_keys", "verification_commands"):
        values = source.get(key)
        if not isinstance(values, list):
            continue
        target_values = target.setdefault(key, [])
        if not isinstance(target_values, list):
            continue
        for value in values:
            _append_unique(target_values, value)
    verification = source.get("verification")
    if isinstance(verification, list):
        target_verification = target.setdefault("verification", [])
        if isinstance(target_verification, list):
            for value in verification:
                _append_unique_json(target_verification, value)


def _aggregate_next_actions(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    for step in steps:
        if step.get("exit_code") == 0:
            continue
        top_source_step = step.get("key")
        for action in step.get("next_actions", []):
            if isinstance(action, dict):
                sourced_action = {"source_step": top_source_step, **action}
                signature = _action_signature(sourced_action)
                if signature not in grouped:
                    grouped[signature] = {
                        **sourced_action,
                        "source_steps": [],
                        "source_reports": [],
                        "source_count": 0,
                    }
                    actions.append(grouped[signature])
                grouped_action = grouped[signature]
                _merge_action_metadata(grouped_action, sourced_action)
                _append_unique(grouped_action["source_steps"], top_source_step)
                _append_unique(grouped_action["source_reports"], sourced_action.get("source_report"))
                for source_report in sourced_action.get("source_reports", []):
                    _append_unique(grouped_action["source_reports"], source_report)
                grouped_action["source_count"] += 1
    return actions


def refresh_production_readiness(
    *,
    env_file: Path | str = EXPECTED_PRODUCTION_ENV_FILE,
    backend_url: str = "http://127.0.0.1:8000",
    timeout: float = 20.0,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    release_gate_runner: ReleaseGateRunner = run_release_gate,
    operator_preflight_runner: OperatorPreflightRunner = run_operator_preflight,
    stack_release_runner: StackReleaseRunner = run_stack_release,
    frontend_release_checks_runner: FrontendReleaseChecksRunner = run_frontend_release_checks,
    summary_runner: SummaryRunner = summarize_production_readiness,
    report_hygiene_checker: ReportHygieneChecker = check_verification_reports,
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

    mismatch = _candidate_env_guard(env_path, root)
    if mismatch is not None:
        action = _candidate_env_action(mismatch)
        report = {
            "report_type": "production_readiness_refresh",
            "status": "blocked",
            "readiness_state": "blocked",
            "release_candidate_ready": False,
            "release_allowed": False,
            "generated_at": timestamp.isoformat(),
            "env_file": str(env_path),
            "candidate_env_mismatch": mismatch,
            "blocking_steps": ["candidate_env_file"],
            "next_action_count": 1,
            "next_actions": [action],
            "refresh_command_plan": _refresh_command_plan(),
            "steps": [],
        }
        return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)

    steps: list[dict[str, Any]] = []
    try:
        release_gate = release_gate_runner(
            backend_url=backend_url,
            timeout=timeout,
            out_dir=destination,
            project_root=root,
            now=timestamp,
            env_file=env_path,
            require_env_file=True,
            compose_config_checker=check_compose_config,
            secret_inventory_validator=validate_secret_inventory,
        )
        steps.append(_step("release_gate", "Release gate", release_gate, exit_code=_release_gate_exit(release_gate)))
    except Exception as exc:
        steps.append(_step_exception("release_gate", "Release gate", exc))

    try:
        operator = operator_preflight_runner(
            env_file=env_path,
            backend_url=backend_url,
            timeout=timeout,
            out_dir=destination,
            project_root=root,
            now=timestamp,
            include_live_gate=False,
        )
        steps.append(
            _step(
                "production_operator_preflight",
                "Production operator preflight",
                operator,
                exit_code=_operator_exit(operator),
            )
        )
    except Exception as exc:
        steps.append(_step_exception("production_operator_preflight", "Production operator preflight", exc))

    try:
        stack = stack_release_runner(
            env_file=env_path,
            backend_url=backend_url,
            timeout=timeout,
            out_dir=destination,
            project_root=root,
            now=timestamp,
            execute=False,
        )
        steps.append(_step("production_stack_release", "Production stack release", stack, exit_code=_stack_exit(stack)))
    except Exception as exc:
        steps.append(_step_exception("production_stack_release", "Production stack release", exc))

    try:
        frontend = frontend_release_checks_runner(
            out_dir=destination,
            project_root=root,
            now=timestamp,
        )
        steps.append(_step("frontend_release_checks", "Frontend release checks", frontend, exit_code=_frontend_exit(frontend)))
    except Exception as exc:
        steps.append(_step_exception("frontend_release_checks", "Frontend release checks", exc))

    try:
        summary = summary_runner(
            report_dir=destination,
            out_dir=destination,
            project_root=root,
            now=timestamp,
        )
        summary_exit = _summary_exit(summary)
        steps.append(
            _step(
                "production_readiness_summary",
                "Production readiness summary",
                summary,
                exit_code=summary_exit,
            )
        )
    except Exception as exc:
        summary = {
            "status": "fail",
            "readiness_state": "blocked",
            "release_allowed": False,
            "blocking_reports": ["production_readiness_summary"],
        }
        steps.append(_step_exception("production_readiness_summary", "Production readiness summary", exc))

    try:
        hygiene = report_hygiene_checker(destination)
        hygiene = _write_hygiene_report(hygiene, out_dir=destination, stamp=stamp, project_root=root)
        hygiene_exit = report_hygiene_exit_code(hygiene, fail_on_issue=True)
        steps.append(
            _step(
                "verification_report_hygiene",
                "Verification report hygiene",
                hygiene,
                exit_code=hygiene_exit,
            )
        )
    except Exception as exc:
        steps.append(_step_exception("verification_report_hygiene", "Verification report hygiene", exc))

    blocking_steps = [str(step["key"]) for step in steps if step.get("exit_code") != 0]
    next_actions = _aggregate_next_actions(steps)
    summary_status = str(summary.get("status") or "blocked")
    report = {
        "report_type": "production_readiness_refresh",
        "status": "pass" if summary_status == "pass" and not blocking_steps else "blocked",
        "readiness_state": summary.get("readiness_state") or ("release_ready" if not blocking_steps else "blocked"),
        "release_candidate_ready": summary_status == "pass" and not blocking_steps,
        "release_allowed": bool(summary.get("release_allowed") is True and not blocking_steps),
        "generated_at": timestamp.isoformat(),
        "env_file": str(env_path),
        "backend_url": "<backend-url>",
        "blocking_steps": blocking_steps,
        "next_action_count": len(next_actions),
        "next_actions": next_actions,
        "refresh_command_plan": _refresh_command_plan(),
        "summary_latest_path": summary.get("latest_path"),
        "digest_latest_path": summary.get("digest_latest_path"),
        "summary_status": summary.get("status"),
        "summary_blocking_reports": summary.get("blocking_reports", []),
        "summary_missing_reports": summary.get("missing_reports", []),
        "summary_stale_reports": summary.get("stale_reports", []),
        "candidate_env_mismatch_count": summary.get("candidate_env_mismatch_count", 0),
        "steps": steps,
    }
    return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)


def refresh_exit_code(result: dict[str, Any], *, fail_on_blocked: bool) -> int:
    if not fail_on_blocked:
        return 0
    return 0 if result.get("status") == "pass" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh all production readiness evidence from one candidate env file.")
    parser.add_argument("--env-file", default=EXPECTED_PRODUCTION_ENV_FILE)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--report-dir", default="docs/verification")
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()
    result = refresh_production_readiness(
        env_file=args.env_file,
        backend_url=args.backend_url,
        timeout=args.timeout,
        out_dir=args.report_dir,
    )
    print(format_cli_json(result))
    return refresh_exit_code(result, fail_on_blocked=args.fail_on_blocked)


if __name__ == "__main__":
    raise SystemExit(main())
