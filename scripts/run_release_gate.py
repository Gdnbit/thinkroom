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

from export_production_preflight import (  # noqa: E402
    export_preflight,
    public_cli_result as public_preflight_cli_result,
    release_gate_exit_code,
)
from scan_public_api_leaks import (  # noqa: E402
    export_public_api_leak_scan,
    leak_scan_exit_code,
    public_cli_result as public_scan_cli_result,
)
from check_verification_reports import (  # noqa: E402
    BLOCKED_MARKERS,
    check_verification_reports,
    exit_code as report_hygiene_exit_code,
)
from validate_production_env import (  # noqa: E402
    validate_env_file,
    validation_exit_code,
)
from validate_secret_inventory import (  # noqa: E402
    validate_secret_inventory,
    validation_exit_code as secret_inventory_validation_exit_code,
)
from check_production_compose_config import check_compose_config  # noqa: E402


ScanExporter = Callable[..., dict[str, Any]]
PreflightExporter = Callable[..., dict[str, Any]]
EnvExporter = Callable[..., dict[str, Any]]
ReportHygieneChecker = Callable[..., dict[str, Any]]
SecretInventoryValidator = Callable[..., dict[str, Any]]
ComposeConfigChecker = Callable[..., dict[str, Any]]
SECRET_SHAPE_PATTERNS = (
    (re.compile(r"sk-or-v1-[A-Za-z0-9_-]+"), "[redacted-openrouter-key]"),
    (re.compile(r"\bsops:[A-Za-z0-9_./:-]+"), "[redacted-secret-source]"),
)
MATERIALIZER_SOURCE_LABEL = "sops:production"
MATERIALIZER_SOURCE_LABEL_PLACEHOLDER = "__RESEARCHOS_MATERIALIZER_SOURCE_LABEL__"


def project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_inside_project(path: Path, project_root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = project_root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Output directory must be inside project: {resolved_root}") from exc
    return resolved_path


def _step(
    key: str,
    label: str,
    result: dict[str, Any],
    *,
    exit_code: int,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": result.get("status") or "unknown",
        "exit_code": exit_code,
        **result,
    }


def _redact_release_gate_text(value: str) -> str:
    text = value.replace(
        f"--source-label {MATERIALIZER_SOURCE_LABEL}",
        f"--source-label {MATERIALIZER_SOURCE_LABEL_PLACEHOLDER}",
    )
    for marker in BLOCKED_MARKERS:
        if marker:
            text = text.replace(marker, "[redacted]")
    text = re.sub(
        r"\bhttps?://(?:127\.0\.0\.1|localhost)(?::\d+)?\b",
        "[backend-url]",
        text,
        flags=re.IGNORECASE,
    )
    for pattern, replacement in SECRET_SHAPE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.replace(MATERIALIZER_SOURCE_LABEL_PLACEHOLDER, MATERIALIZER_SOURCE_LABEL)


def _redact_release_gate_markers(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_release_gate_markers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_release_gate_markers(item) for item in value]
    if isinstance(value, str):
        return _redact_release_gate_text(value)
    return value


def public_cli_result(result: dict[str, Any]) -> dict[str, Any]:
    return _redact_release_gate_markers(public_preflight_cli_result(public_scan_cli_result(result)))


def format_cli_json(result: dict[str, Any]) -> str:
    return json.dumps(public_cli_result(result), ensure_ascii=True, indent=2)


def _md_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _md_code(value: Any) -> str:
    text = _md_value(value).replace("`", "'")
    return f"`{text}`" if text else "`unknown`"


def _md_code_list(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "-"
    return ", ".join(_md_code(item) for item in values)


def _md_verifications(verifications: Any) -> str:
    if not isinstance(verifications, list) or not verifications:
        return "-"
    rendered: list[str] = []
    for item in verifications:
        if not isinstance(item, dict):
            continue
        kind = _md_value(item.get("kind"))
        target = _md_value(item.get("target"))
        expectation = _md_value(item.get("expectation"))
        parts = [part for part in [kind, target, expectation] if part]
        if parts:
            rendered.append(" / ".join(parts))
    return "; ".join(rendered) if rendered else "-"


def _secret_inventory_validation_summary(public: dict[str, Any]) -> str:
    steps = public.get("steps")
    if not isinstance(steps, list):
        return "-"
    for step in steps:
        if not isinstance(step, dict) or step.get("key") != "secret_inventory_validation":
            continue
        summary = step.get("summary")
        if not isinstance(summary, dict):
            return "-"
        return (
            f"pass={_md_value(summary.get('pass') or 0)}, "
            f"fail={_md_value(summary.get('fail') or 0)}, "
            f"total={_md_value(summary.get('total') or 0)}"
        )
    return "-"


def format_release_action_plan(result: dict[str, Any]) -> str:
    public = public_cli_result(result)
    lines = [
        "# Release Gate Action Plan",
        "",
        f"Generated: {_md_value(public.get('exported_at'))}",
        f"Status: {_md_value(public.get('status'))}",
        f"Release allowed: {str(bool(public.get('release_allowed'))).lower()}",
        f"Run mode: {_md_value(public.get('run_mode'))}",
        f"Gate exit code: {_md_value(public.get('gate_exit_code'))}",
        f"Env file issue count: {_md_value(public.get('env_file_issue_count') or 0)}",
        f"Env file issue codes: {_md_code_list(public.get('env_file_issue_codes'))}",
        f"Required config key count: {_md_value(public.get('required_config_key_count') or 0)}",
        f"Required config keys: {_md_code_list(public.get('required_config_keys'))}",
        f"Required config key action count: {_md_value(public.get('required_config_key_action_count') or 0)}",
        f"Direct required config key count: {_md_value(public.get('direct_required_config_key_count') or 0)}",
        f"Direct required config keys: {_md_code_list(public.get('direct_required_config_keys'))}",
        f"Required secret config key count: {_md_value(public.get('required_secret_config_key_count') or 0)}",
        f"Required secret config keys: {_md_code_list(public.get('required_secret_config_keys'))}",
        f"Production compose config: {_md_value(public.get('production_compose_config_latest_path')) or '-'}",
        f"Secret inventory: {_md_value(public.get('secret_inventory_markdown_latest_path')) or '-'}",
        f"Secret inventory validation: {_md_value(public.get('secret_inventory_validation_latest_path')) or '-'}",
        f"Secret inventory validation summary: {_secret_inventory_validation_summary(public)}",
        "",
        "## Source Provenance Runbook",
        "",
        "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`",
        "- Fast path: set one trusted `SECRETS_SOURCE` marker when all production secrets and secret-bearing config are injected by the same controlled source.",
        "- Per-key path: set trusted `*_SOURCE` markers when different keys are injected by different controlled sources.",
        "- Validate: `python scripts/validate_production_env.py --env-file <candidate-production-env> --fail-on-error` and then `python scripts/run_release_gate.py --require-env-file --env-file <candidate-production-env>`.",
        "",
        "## Blocking Steps",
    ]

    blocking_steps = public.get("blocking_steps")
    if isinstance(blocking_steps, list) and blocking_steps:
        lines.extend(f"- {_md_code(step)}" for step in blocking_steps)
    else:
        lines.append("- None")

    lines.extend(["", "## Execution Failures"])
    execution_failure_steps = public.get("execution_failure_steps")
    if isinstance(execution_failure_steps, list) and execution_failure_steps:
        lines.extend(f"- {_md_code(step)}" for step in execution_failure_steps)
    else:
        lines.append("- None")

    lines.extend(["", "## Required Config Key Checklist"])
    config_key_actions = public.get("required_config_key_actions")
    if isinstance(config_key_actions, list) and config_key_actions:
        for item in config_key_actions:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"- [ ] {_md_code(item.get('config_key'))}",
                    f"  - Source steps: {_md_code_list(item.get('source_steps'))}",
                    f"  - Action keys: {_md_code_list(item.get('action_keys'))}",
                    f"  - Categories: {_md_code_list(item.get('categories'))}",
                    f"  - Owners: {_md_code_list(item.get('owners'))}",
                    f"  - Secret update: {str(bool(item.get('requires_secret_update'))).lower()}",
                    f"  - Secret config key: {str(bool(item.get('secret_config_key'))).lower()}",
                    f"  - Context only: {str(bool(item.get('context_only'))).lower()}",
                    *(
                        [f"  - Alternative config keys: {_md_code_list(item.get('alternative_config_keys'))}"]
                        if isinstance(item.get("alternative_config_keys"), list)
                        and item.get("alternative_config_keys")
                        else []
                    ),
                    *(
                        [
                            "  - Source provenance target config keys: "
                            f"{_md_code_list(item.get('source_provenance_target_config_keys'))}"
                        ]
                        if isinstance(item.get("source_provenance_target_config_keys"), list)
                        and item.get("source_provenance_target_config_keys")
                        else []
                    ),
                    f"  - Verification: {_md_verifications(item.get('verification'))}",
                ]
            )
    else:
        lines.append("- No config-key-scoped actions.")

    lines.extend(["", "## Required Actions"])
    groups = public.get("required_action_groups")
    actions = public.get("required_actions")
    action_items = actions if isinstance(actions, list) else []
    if isinstance(groups, list) and groups:
        for group in groups:
            if not isinstance(group, dict):
                continue
            lines.extend(
                [
                    "",
                    f"### {_md_value(group.get('label'))} ({_md_code(group.get('source_step'))})",
                    f"- Status: {_md_value(group.get('status'))}",
                    f"- Action count: {_md_value(group.get('action_count'))}",
                    f"- Config keys: {_md_code_list(group.get('config_keys'))}",
                    f"- Secret update actions: {_md_value(group.get('requires_secret_update_count') or 0)}",
                    f"- Secret config keys: {_md_code_list(group.get('secret_config_keys'))}",
                ]
            )
            source_step = group.get("source_step")
            for item in action_items:
                if not isinstance(item, dict) or item.get("source_step") != source_step:
                    continue
                lines.extend(
                    [
                        f"- [ ] {_md_code(item.get('key'))} - {_md_value(item.get('action'))}",
                        f"  - Owner: {_md_value(item.get('owner')) or 'unassigned'}",
                        f"  - Category: {_md_value(item.get('category')) or _md_value(item.get('key'))}",
                        f"  - Config keys: {_md_code_list(item.get('config_keys'))}",
                        *(
                            [f"  - Alternative config keys: {_md_code_list(item.get('alternative_config_keys'))}"]
                            if isinstance(item.get("alternative_config_keys"), list)
                            and item.get("alternative_config_keys")
                            else []
                        ),
                        *(
                            [
                                "  - Source provenance target config keys: "
                                f"{_md_code_list(item.get('source_provenance_target_config_keys'))}"
                            ]
                            if isinstance(item.get("source_provenance_target_config_keys"), list)
                            and item.get("source_provenance_target_config_keys")
                            else []
                        ),
                        f"  - Verification: {_md_verifications(item.get('verification'))}",
                        *(
                            [f"  - Verification commands: {_md_code_list(item.get('verification_commands'))}"]
                            if isinstance(item.get("verification_commands"), list)
                            and item.get("verification_commands")
                            else []
                        ),
                    ]
                )
    elif action_items:
        for item in action_items:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"- [ ] {_md_code(item.get('key'))} - {_md_value(item.get('action'))}",
                    f"  - Source step: {_md_code(item.get('source_step'))}",
                    f"  - Owner: {_md_value(item.get('owner')) or 'unassigned'}",
                    f"  - Config keys: {_md_code_list(item.get('config_keys'))}",
                    *(
                        [f"  - Alternative config keys: {_md_code_list(item.get('alternative_config_keys'))}"]
                        if isinstance(item.get("alternative_config_keys"), list)
                        and item.get("alternative_config_keys")
                        else []
                    ),
                    *(
                        [
                            "  - Source provenance target config keys: "
                            f"{_md_code_list(item.get('source_provenance_target_config_keys'))}"
                        ]
                        if isinstance(item.get("source_provenance_target_config_keys"), list)
                        and item.get("source_provenance_target_config_keys")
                        else []
                    ),
                    *(
                        [f"  - Verification commands: {_md_code_list(item.get('verification_commands'))}"]
                        if isinstance(item.get("verification_commands"), list)
                        and item.get("verification_commands")
                        else []
                    ),
                ]
            )
    else:
        lines.append("No required actions.")

    lines.extend(["", "## Step Summary"])
    steps = public.get("steps")
    if isinstance(steps, list) and steps:
        for step in steps:
            if not isinstance(step, dict):
                continue
            lines.append(
                f"- {_md_code(step.get('key'))}: status={_md_value(step.get('status'))}, "
                f"exit_code={_md_value(step.get('exit_code'))}"
            )
    else:
        lines.append("- No steps recorded.")

    return "\n".join(lines) + "\n"


def _release_gate_verification_command(*, backend_url: str, env_file: Path | str | None) -> str:
    parts = ["python", "scripts/run_release_gate.py", "--require-env-file"]
    parts.extend(["--env-file", Path(env_file or "apps/backend/.env.prod").as_posix()])
    parts.extend(["--backend-url", backend_url.rstrip("/")])
    return " ".join(parts)


def _verification_commands_from_targets(
    item: dict[str, Any],
    *,
    backend_url: str,
    env_file: Path | str | None,
) -> list[str]:
    if isinstance(item.get("verification_commands"), list):
        commands = [str(command).strip() for command in item["verification_commands"]]
        return [command for command in commands if command]
    verifications = item.get("verification")
    if not isinstance(verifications, list):
        return []
    for verification in verifications:
        if not isinstance(verification, dict):
            continue
        if verification.get("kind") in {"endpoint", "script", "report"}:
            return [_release_gate_verification_command(backend_url=backend_url, env_file=env_file)]
    return []


def _required_actions_from_blocking_steps(
    steps: list[dict[str, Any]],
    *,
    backend_url: str = "http://127.0.0.1:8000",
    env_file: Path | str | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for step in steps:
        if step.get("exit_code") == 0:
            continue
        step_actions = step.get("next_actions")
        if not isinstance(step_actions, list):
            continue
        for item in step_actions:
            if not isinstance(item, dict):
                continue
            verification_commands = _verification_commands_from_targets(
                item,
                backend_url=backend_url,
                env_file=env_file,
            )
            action = {
                "source_step": step.get("key"),
                **item,
            }
            if verification_commands:
                action["verification_commands"] = verification_commands
            actions.append(
                action
            )
    return actions


def _append_unique(items: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in items:
        items.append(text)


def _env_file_issue_summary(env_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(env_result, dict):
        return {"env_file_issue_count": 0, "env_file_issue_codes": []}
    codes: list[str] = []
    raw_codes = env_result.get("env_file_issue_codes")
    if isinstance(raw_codes, list):
        for code in raw_codes:
            _append_unique(codes, code)
    if codes:
        return {"env_file_issue_count": len(codes), "env_file_issue_codes": codes}
    try:
        count = max(0, int(env_result.get("env_file_issue_count") or 0))
    except (TypeError, ValueError):
        count = 0
    return {"env_file_issue_count": count, "env_file_issue_codes": []}


def _step_output_path(steps: list[dict[str, Any]], key: str, field: str) -> str | None:
    for step in steps:
        if step.get("key") != key:
            continue
        value = step.get(field)
        return str(value) if value else None
    return None


def _required_action_groups(
    steps: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    step_meta = {
        str(step.get("key")): {
            "label": step.get("label") or step.get("key"),
            "status": step.get("status") or "unknown",
        }
        for step in steps
    }
    groups: dict[str, dict[str, Any]] = {}
    for item in actions:
        source_step = str(item.get("source_step") or "unknown")
        meta = step_meta.get(source_step, {"label": source_step, "status": "unknown"})
        group = groups.setdefault(
            source_step,
            {
                "source_step": source_step,
                "label": meta["label"],
                "status": meta["status"],
                "action_count": 0,
                "action_keys": [],
                "categories": {},
                "owner_counts": {},
                "config_keys": [],
                "requires_secret_update_count": 0,
                "secret_config_keys": [],
            },
        )
        group["action_count"] += 1
        _append_unique(group["action_keys"], item.get("key"))
        category = str(item.get("category") or item.get("key") or "uncategorized")
        group["categories"][category] = group["categories"].get(category, 0) + 1
        owner = str(item.get("owner") or "unassigned")
        group["owner_counts"][owner] = group["owner_counts"].get(owner, 0) + 1
        config_keys = item.get("config_keys") if isinstance(item.get("config_keys"), list) else []
        for config_key in config_keys:
            _append_unique(group["config_keys"], config_key)
        secret_config_keys = _required_secret_config_keys([item])
        if item.get("requires_secret_update") is True or secret_config_keys:
            group["requires_secret_update_count"] += 1
        for config_key in secret_config_keys:
            _append_unique(group["secret_config_keys"], config_key)
    return list(groups.values())


def _required_config_keys(actions: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for item in actions:
        config_keys = item.get("config_keys") if isinstance(item.get("config_keys"), list) else []
        for config_key in config_keys:
            _append_unique(keys, config_key)
    return keys


def _is_context_only_config_action(item: dict[str, Any]) -> bool:
    return item.get("key") == "production_dry_run" or item.get("category") == "production_profile"


def _direct_required_config_keys(actions: list[dict[str, Any]]) -> list[str]:
    return _required_config_keys([item for item in actions if not _is_context_only_config_action(item)])


def _looks_like_secret_config_key(key: Any) -> bool:
    text = str(key or "").upper()
    if text == "SECRETS_SOURCE" or text.endswith("_SOURCE"):
        return False
    if text == "DATABASE_URL":
        return True
    return any(marker in text for marker in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DSN"))


def _item_contains_uri_credentials(item: dict[str, Any]) -> bool:
    uri = item.get("uri")
    return isinstance(uri, dict) and uri.get("contains_credentials") is True


def _required_secret_config_keys(actions: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for item in actions:
        if item.get("requires_secret_update") is not True and not _item_contains_uri_credentials(item):
            continue
        config_keys = item.get("config_keys") if isinstance(item.get("config_keys"), list) else []
        for config_key in config_keys:
            if _looks_like_secret_config_key(config_key) or _item_contains_uri_credentials(item):
                _append_unique(keys, config_key)
    return keys


def _append_unique_dict(items: list[dict[str, Any]], value: Any) -> None:
    if not isinstance(value, dict):
        return
    marker = json.dumps(value, ensure_ascii=True, sort_keys=True)
    existing = {json.dumps(item, ensure_ascii=True, sort_keys=True) for item in items}
    if marker not in existing:
        items.append(value)


def _required_config_key_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in actions:
        config_keys = item.get("config_keys") if isinstance(item.get("config_keys"), list) else []
        if not config_keys:
            continue
        item_secret_keys = set(_required_secret_config_keys([item]))
        item_is_context_only = _is_context_only_config_action(item)
        for config_key in config_keys:
            key = str(config_key or "").strip()
            if not key:
                continue
            group = groups.setdefault(
                key,
                {
                    "config_key": key,
                    "source_steps": [],
                    "action_keys": [],
                    "categories": [],
                    "owners": [],
                    "requires_secret_update": False,
                    "secret_config_key": False,
                    "context_only": True,
                    "alternative_config_keys": [],
                    "source_provenance_target_config_keys": [],
                    "verification": [],
                },
            )
            _append_unique(group["source_steps"], item.get("source_step"))
            _append_unique(group["action_keys"], item.get("key"))
            _append_unique(group["categories"], item.get("category") or item.get("key"))
            _append_unique(group["owners"], item.get("owner") or "unassigned")
            if item.get("requires_secret_update") is True or key in item_secret_keys:
                group["requires_secret_update"] = True
            if key in item_secret_keys:
                group["secret_config_key"] = True
            if not item_is_context_only:
                group["context_only"] = False
            alternative_config_keys = (
                item.get("alternative_config_keys")
                if isinstance(item.get("alternative_config_keys"), list)
                else []
            )
            for alternative_config_key in alternative_config_keys:
                _append_unique(group["alternative_config_keys"], alternative_config_key)
            target_config_keys = (
                item.get("source_provenance_target_config_keys")
                if isinstance(item.get("source_provenance_target_config_keys"), list)
                else []
            )
            for target_config_key in target_config_keys:
                _append_unique(group["source_provenance_target_config_keys"], target_config_key)
            verifications = item.get("verification")
            if isinstance(verifications, list):
                for verification in verifications:
                    _append_unique_dict(group["verification"], verification)
    result: list[dict[str, Any]] = []
    for group in groups.values():
        if not group["alternative_config_keys"]:
            group.pop("alternative_config_keys", None)
        if not group["source_provenance_target_config_keys"]:
            group.pop("source_provenance_target_config_keys", None)
        result.append(group)
    return result


def _secret_inventory_items(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for action in actions:
        config_keys = action.get("config_keys") if isinstance(action.get("config_keys"), list) else []
        if not config_keys:
            continue
        uri_contains_credentials = _item_contains_uri_credentials(action)
        for config_key in config_keys:
            key = str(config_key or "").strip()
            if not key:
                continue
            if not (_looks_like_secret_config_key(key) or uri_contains_credentials):
                continue
            item = items.setdefault(
                key,
                {
                    "key": key,
                    "classification": "credential_bearing_uri" if uri_contains_credentials else "secret",
                    "recommended_source": "SOPS or deployment secret store",
                    "source_steps": [],
                    "action_keys": [],
                    "categories": [],
                    "owners": [],
                    "verification": [],
                },
            )
            if uri_contains_credentials:
                item["classification"] = "credential_bearing_uri"
            _append_unique(item["source_steps"], action.get("source_step"))
            _append_unique(item["action_keys"], action.get("key"))
            _append_unique(item["categories"], action.get("category") or action.get("key"))
            _append_unique(item["owners"], action.get("owner") or "unassigned")
            verifications = action.get("verification")
            if isinstance(verifications, list):
                for verification in verifications:
                    _append_unique_dict(item["verification"], verification)
    return list(items.values())


def _source_provenance_keys(secret_config_keys: list[str], actions: list[dict[str, Any]]) -> list[str]:
    keys = ["SECRETS_SOURCE"]
    for secret_config_key in secret_config_keys:
        _append_unique(keys, f"{secret_config_key}_SOURCE")
    for action in actions:
        for field in ("config_keys", "alternative_config_keys"):
            values = action.get(field) if isinstance(action.get(field), list) else []
            for value in values:
                text = str(value or "").strip()
                if text == "SECRETS_SOURCE" or text.endswith("_SOURCE"):
                    _append_unique(keys, text)
    return keys


def _source_provenance_target_config_keys(actions: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for action in actions:
        values = (
            action.get("source_provenance_target_config_keys")
            if isinstance(action.get("source_provenance_target_config_keys"), list)
            else []
        )
        for value in values:
            _append_unique(keys, value)
    return keys


def build_secret_inventory(
    *,
    actions: list[dict[str, Any]],
    timestamp: datetime,
    release_gate_status: str,
    release_allowed: bool,
    export_path: Path,
    latest_path: Path,
    markdown_path: Path,
    markdown_latest_path: Path,
) -> dict[str, Any]:
    items = _secret_inventory_items(actions)
    keys = [item["key"] for item in items]
    source_provenance_keys = _source_provenance_keys(keys, actions)
    per_key_source_provenance_keys = source_provenance_keys[1:]
    source_provenance_target_config_keys = _source_provenance_target_config_keys(actions)
    return {
        "report_type": "release_gate_secret_inventory",
        "status": "required" if items else "empty",
        "generated_at": timestamp.isoformat(),
        "release_gate_status": release_gate_status,
        "release_allowed": release_allowed,
        "secret_config_key_count": len(keys),
        "secret_config_keys": keys,
        "source_provenance_key": "SECRETS_SOURCE",
        "source_provenance_key_count": len(source_provenance_keys),
        "source_provenance_keys": source_provenance_keys,
        "per_key_source_provenance_key_count": len(per_key_source_provenance_keys),
        "per_key_source_provenance_keys": per_key_source_provenance_keys,
        "source_provenance_target_config_key_count": len(source_provenance_target_config_keys),
        "source_provenance_target_config_keys": source_provenance_target_config_keys,
        "items": items,
        "recommended_default_source": "SOPS or deployment secret store",
        "export_path": str(export_path),
        "latest_path": str(latest_path),
        "markdown_path": str(markdown_path),
        "markdown_latest_path": str(markdown_latest_path),
        "policy_basis": [
            "OWASP Secrets Management: production secrets must be managed through controlled secret stores and never echoed in reports.",
            "Twelve-Factor Config: deploy-specific config belongs in the environment, not code.",
            "Project production guard: release gate artifacts may list secret keys but must not contain secret values.",
        ],
    }


def format_secret_inventory_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# Release Gate Secret Inventory",
        "",
        f"Generated: {_md_value(inventory.get('generated_at'))}",
        f"Status: {_md_value(inventory.get('status'))}",
        f"Release gate status: {_md_value(inventory.get('release_gate_status'))}",
        f"Release allowed: {str(bool(inventory.get('release_allowed'))).lower()}",
        f"Secret config key count: {_md_value(inventory.get('secret_config_key_count') or 0)}",
        f"Secret config keys: {_md_code_list(inventory.get('secret_config_keys'))}",
        f"Global source marker: {_md_code(inventory.get('source_provenance_key'))}",
        f"Per-key source markers: {_md_code_list(inventory.get('per_key_source_provenance_keys'))}",
        f"Source provenance target config keys: {_md_code_list(inventory.get('source_provenance_target_config_keys'))}",
        f"Recommended default source: {_md_value(inventory.get('recommended_default_source'))}",
        "",
        "## Required Secret Keys",
    ]
    items = inventory.get("items")
    if not isinstance(items, list) or not items:
        lines.append("- No secret config keys required by the current gate result.")
    else:
        for item in items:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"- [ ] {_md_code(item.get('key'))}",
                    f"  - Classification: {_md_value(item.get('classification'))}",
                    f"  - Recommended source: {_md_value(item.get('recommended_source'))}",
                    f"  - Source steps: {_md_code_list(item.get('source_steps'))}",
                    f"  - Action keys: {_md_code_list(item.get('action_keys'))}",
                    f"  - Categories: {_md_code_list(item.get('categories'))}",
                    f"  - Owners: {_md_code_list(item.get('owners'))}",
                    f"  - Verification: {_md_verifications(item.get('verification'))}",
                ]
            )
    lines.extend(
        [
            "",
            "## Source Provenance Runbook",
            "- Runbook: `docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md`",
            "- Use this runbook to fill `SECRETS_SOURCE` or per-key `*_SOURCE` metadata without exposing secret values.",
            "",
            "## Handling Rules",
            "- Store real values only in SOPS or the deployment secret store.",
            "- Do not write real values into `.env`, verification reports, logs, screenshots, or documentation.",
            "- Validate current injected environment: `python scripts/validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --fail-on-error`.",
            "- Validate a candidate production env file: `python scripts/validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --env-file <candidate-production-env> --fail-on-error`.",
            "- Rerun `scripts/run_release_gate.py` after injection and confirm this inventory is empty or release gate status is pass.",
        ]
    )
    return "\n".join(lines) + "\n"


def _runtime_config_binding_result(
    *,
    candidate_deployment_config_id: str,
    preflight_result: dict[str, Any],
) -> dict[str, Any]:
    runtime_identity = preflight_result.get("runtime_config_identity")
    runtime_identity = runtime_identity if isinstance(runtime_identity, dict) else {}
    candidate_id = (candidate_deployment_config_id or "").strip()
    runtime_id = str(runtime_identity.get("deployment_config_id") or "").strip()
    matches = bool(candidate_id and runtime_id and candidate_id == runtime_id)
    next_actions: list[dict[str, Any]] = []
    if not matches:
        next_actions.append(
            {
                "key": "runtime_config_binding",
                "label": "Runtime config binding",
                "status": "fail",
                "category": "configuration",
                "action": (
                    "Restart the backend with the same DEPLOYMENT_CONFIG_ID that was validated from --env-file, "
                    "then rerun the release gate."
                ),
                "owner": "deployment_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": ["DEPLOYMENT_CONFIG_ID"],
                "verification": [
                    {
                        "kind": "script",
                        "target": "scripts/run_release_gate.py",
                        "expectation": "runtime_config_binding.status=pass",
                    }
                ],
            }
        )
    return {
        "status": "pass" if matches else "fail",
        "candidate_deployment_config_id": candidate_id,
        "runtime_config_identity": runtime_identity,
        "match": matches,
        "next_actions": next_actions,
    }


def _step_exception_result(*, key: str, label: str, exc: Exception) -> dict[str, Any]:
    return {
        "status": "fail",
        "exception_type": type(exc).__name__,
        "next_actions": [
            {
                "key": f"{key}_execution",
                "label": f"{label} execution",
                "status": "fail",
                "category": "release_gate_execution",
                "action": f"Fix the {label} release gate step and rerun scripts/run_release_gate.py.",
                "owner": "release_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
                "verification": [
                    {
                        "kind": "script",
                        "target": "scripts/run_release_gate.py",
                        "expectation": f"{key}.status=pass",
                    }
                ],
            }
        ],
    }


def run_release_gate(
    *,
    backend_url: str,
    timeout: float = 20.0,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    env_file: Path | str | None = None,
    require_env_file: bool = False,
    report_only: bool = False,
    env_exporter: EnvExporter = validate_env_file,
    scan_exporter: ScanExporter = export_public_api_leak_scan,
    preflight_exporter: PreflightExporter = export_preflight,
    report_hygiene_checker: ReportHygieneChecker = check_verification_reports,
    secret_inventory_validator: SecretInventoryValidator | None = None,
    compose_config_checker: ComposeConfigChecker | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    destination = Path(out_dir) if out_dir is not None else root / "docs" / "verification"
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")

    steps: list[dict[str, Any]] = []
    env_result: dict[str, Any] | None = None
    env_exit: int | None = None
    if env_file is not None:
        try:
            env_result = env_exporter(
                Path(env_file),
                out_dir=destination,
                project_root=root,
                now=timestamp,
            )
            env_exit = validation_exit_code(env_result, fail_on_error=True)
        except Exception as exc:
            env_result = _step_exception_result(
                key="production_env_validation",
                label="Production env validation",
                exc=exc,
            )
            env_exit = 2
        steps.append(
            _step("production_env_validation", "Production env validation", env_result, exit_code=env_exit)
        )
    elif require_env_file:
        env_result = {
            "status": "missing",
            "release_allowed": False,
            "env_file_issue_count": 1,
            "env_file_issue_codes": ["env_file_required"],
            "next_actions": [
                {
                    "key": "env_file",
                    "label": "Production env file",
                    "status": "fail",
                    "category": "configuration",
                    "action": "Pass --env-file <candidate-production-env> so release gate validates deployment configuration before runtime checks.",
                    "owner": "deployment_operator",
                    "can_auto_fix": False,
                    "requires_secret_update": True,
                    "production_required": True,
                    "config_keys": [],
                    "verification": [
                        {
                            "kind": "script",
                            "target": "scripts/run_release_gate.py",
                            "expectation": "--env-file is provided and production_env_validation.status=pass",
                        }
                    ],
                }
            ],
        }
        steps.append(
            _step("production_env_validation", "Production env validation", env_result, exit_code=2)
        )

    if env_file is not None and compose_config_checker is not None:
        try:
            compose_result = compose_config_checker(
                env_file=Path(env_file),
                out_dir=destination,
                project_root=root,
                now=timestamp,
            )
            compose_exit = 0 if compose_result.get("status") == "pass" else 2
        except Exception as exc:
            compose_result = _step_exception_result(
                key="production_compose_config",
                label="Production compose config",
                exc=exc,
            )
            compose_exit = 2
        steps.append(
            _step(
                "production_compose_config",
                "Production compose config",
                compose_result,
                exit_code=compose_exit,
            )
        )

    try:
        scan_result = scan_exporter(
            backend_url=backend_url,
            timeout=timeout,
            out_dir=destination,
            project_root=root,
            now=timestamp,
        )
        scan_exit = leak_scan_exit_code(scan_result, fail_on_leak=True)
    except Exception as exc:
        scan_result = _step_exception_result(key="public_api_leak_scan", label="Public API leak scan", exc=exc)
        scan_exit = 2

    try:
        preflight_result = preflight_exporter(
            backend_url=backend_url,
            timeout=timeout,
            out_dir=destination,
            project_root=root,
            now=timestamp,
        )
        preflight_exit = release_gate_exit_code(preflight_result, fail_on_not_ready=True)
    except Exception as exc:
        preflight_result = _step_exception_result(key="production_preflight", label="Production preflight", exc=exc)
        preflight_exit = 2

    export_path = destination / f"release-gate-{stamp}.json"
    latest_path = destination / "release-gate-latest.json"
    action_plan_path = destination / f"release-gate-action-plan-{stamp}.md"
    action_plan_latest_path = destination / "release-gate-action-plan-latest.md"
    secret_inventory_path = destination / f"release-gate-secret-inventory-{stamp}.json"
    secret_inventory_latest_path = destination / "release-gate-secret-inventory-latest.json"
    secret_inventory_markdown_path = destination / f"release-gate-secret-inventory-{stamp}.md"
    secret_inventory_markdown_latest_path = destination / "release-gate-secret-inventory-latest.md"
    steps.extend(
        [
            _step("public_api_leak_scan", "Public API leak scan", scan_result, exit_code=scan_exit),
            _step("production_preflight", "Production preflight", preflight_result, exit_code=preflight_exit),
        ]
    )
    if env_file is not None and env_exit == 0 and preflight_exit == 0 and env_result is not None:
        binding_result = _runtime_config_binding_result(
            candidate_deployment_config_id=str(env_result.get("deployment_config_id") or ""),
            preflight_result=preflight_result,
        )
        binding_exit = 0 if binding_result["status"] == "pass" else 2
        steps.append(_step("runtime_config_binding", "Runtime config binding", binding_result, exit_code=binding_exit))
    pre_inventory_gate_exit = 0 if all(step["exit_code"] == 0 for step in steps) else 2
    pre_inventory_actions = _required_actions_from_blocking_steps(
        steps,
        backend_url=backend_url,
        env_file=env_file,
    )
    secret_inventory = build_secret_inventory(
        actions=pre_inventory_actions,
        timestamp=timestamp,
        release_gate_status="pass" if pre_inventory_gate_exit == 0 else "blocked",
        release_allowed=pre_inventory_gate_exit == 0,
        export_path=secret_inventory_path,
        latest_path=secret_inventory_latest_path,
        markdown_path=secret_inventory_markdown_path,
        markdown_latest_path=secret_inventory_markdown_latest_path,
    )
    secret_inventory_json = json.dumps(public_cli_result(secret_inventory), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    secret_inventory_path.write_text(secret_inventory_json, encoding="utf-8", newline="\n")
    secret_inventory_latest_path.write_text(secret_inventory_json, encoding="utf-8", newline="\n")
    secret_inventory_markdown_text = format_secret_inventory_markdown(public_cli_result(secret_inventory))
    secret_inventory_markdown_path.write_text(secret_inventory_markdown_text, encoding="utf-8", newline="\n")
    secret_inventory_markdown_latest_path.write_text(secret_inventory_markdown_text, encoding="utf-8", newline="\n")

    secret_inventory_validation_result: dict[str, Any] | None = None
    secret_inventory_validation_exit: int | None = None
    if secret_inventory_validator is not None:
        try:
            secret_inventory_validation_result = secret_inventory_validator(
                secret_inventory_path,
                env_file=Path(env_file) if env_file is not None else None,
                out_dir=destination,
                project_root=root,
                now=timestamp,
            )
            secret_inventory_validation_exit = secret_inventory_validation_exit_code(
                secret_inventory_validation_result,
                fail_on_error=True,
            )
        except Exception as exc:
            secret_inventory_validation_result = _step_exception_result(
                key="secret_inventory_validation",
                label="Secret inventory validation",
                exc=exc,
            )
            secret_inventory_validation_exit = 2
        steps.append(
            _step(
                "secret_inventory_validation",
                "Secret inventory validation",
                secret_inventory_validation_result,
                exit_code=secret_inventory_validation_exit,
            )
        )

    try:
        hygiene_result = report_hygiene_checker(destination)
        hygiene_exit = report_hygiene_exit_code(hygiene_result, fail_on_issue=True)
    except Exception as exc:
        hygiene_result = _step_exception_result(
            key="verification_report_hygiene",
            label="Verification report hygiene",
            exc=exc,
        )
        hygiene_exit = 2
    steps.append(
        _step(
            "verification_report_hygiene",
            "Verification report hygiene",
            hygiene_result,
            exit_code=hygiene_exit,
        )
    )

    gate_exit = 0 if all(step["exit_code"] == 0 for step in steps) else 2
    exit_code = 0 if report_only else gate_exit
    execution_failure_steps = [str(step.get("key")) for step in steps if step.get("exception_type")]
    required_actions = _required_actions_from_blocking_steps(
        steps,
        backend_url=backend_url,
        env_file=env_file,
    )
    required_action_groups = _required_action_groups(steps, required_actions)
    required_config_keys = _required_config_keys(required_actions)
    required_config_key_actions = _required_config_key_actions(required_actions)
    direct_required_config_keys = _direct_required_config_keys(required_actions)
    required_secret_config_keys = _required_secret_config_keys(required_actions)
    env_file_issue_summary = _env_file_issue_summary(env_result)
    result = {
        "report_type": "release_gate",
        "status": "pass" if gate_exit == 0 else "blocked",
        "gate_exit_code": gate_exit,
        "exit_code": exit_code,
        "cli_exit_code": exit_code,
        "report_only": report_only,
        "run_mode": "report_only" if report_only else "strict",
        "release_allowed": gate_exit == 0,
        "env_validation_required": require_env_file,
        "env_validation_present": env_file is not None,
        **env_file_issue_summary,
        "blocking_steps": [step["key"] for step in steps if step["exit_code"] != 0],
        "step_exception_count": len(execution_failure_steps),
        "execution_failure_steps": execution_failure_steps,
        "required_action_count": len(required_actions),
        "required_actions": required_actions,
        "required_action_groups": required_action_groups,
        "required_config_key_count": len(required_config_keys),
        "required_config_keys": required_config_keys,
        "required_config_key_action_count": len(required_config_key_actions),
        "required_config_key_actions": required_config_key_actions,
        "direct_required_config_key_count": len(direct_required_config_keys),
        "direct_required_config_keys": direct_required_config_keys,
        "required_secret_config_key_count": len(required_secret_config_keys),
        "required_secret_config_keys": required_secret_config_keys,
        "exported_at": timestamp.isoformat(),
        "export_path": str(export_path),
        "latest_path": str(latest_path),
        "action_plan_path": str(action_plan_path),
        "action_plan_latest_path": str(action_plan_latest_path),
        "production_compose_config_path": _step_output_path(
            steps,
            "production_compose_config",
            "export_path",
        ),
        "production_compose_config_latest_path": _step_output_path(
            steps,
            "production_compose_config",
            "latest_path",
        ),
        "secret_inventory_path": str(secret_inventory_path),
        "secret_inventory_latest_path": str(secret_inventory_latest_path),
        "secret_inventory_markdown_path": str(secret_inventory_markdown_path),
        "secret_inventory_markdown_latest_path": str(secret_inventory_markdown_latest_path),
        "secret_inventory_validation_path": (
            str(secret_inventory_validation_result.get("export_path"))
            if isinstance(secret_inventory_validation_result, dict) and secret_inventory_validation_result.get("export_path")
            else None
        ),
        "secret_inventory_validation_latest_path": (
            str(secret_inventory_validation_result.get("latest_path"))
            if isinstance(secret_inventory_validation_result, dict) and secret_inventory_validation_result.get("latest_path")
            else None
        ),
        "steps": steps,
        "secret_inventory": secret_inventory,
    }
    action_plan_text = format_release_action_plan(result)
    action_plan_path.write_text(action_plan_text, encoding="utf-8", newline="\n")
    action_plan_latest_path.write_text(action_plan_text, encoding="utf-8", newline="\n")
    text = json.dumps(public_cli_result(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the production release gate in the required order.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--env-file", default=None, help="Optional production env file to validate before runtime gates.")
    parser.add_argument("--require-env-file", action="store_true", help="Block the gate when --env-file is not provided.")
    parser.add_argument("--report-only", action="store_true", help="Generate reports but always exit 0.")
    args = parser.parse_args()

    result = run_release_gate(
        backend_url=args.backend_url,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        timeout=args.timeout,
        env_file=Path(args.env_file) if args.env_file else None,
        require_env_file=args.require_env_file,
        report_only=args.report_only,
        secret_inventory_validator=validate_secret_inventory,
        compose_config_checker=check_compose_config if args.env_file else None,
    )
    print(format_cli_json(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
