from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPORT_DIR = SCRIPT_DIR.parent / "docs" / "verification"
DEFAULT_OUT_DIR = DEFAULT_REPORT_DIR
EXPECTED_PRODUCTION_ENV_FILE = "apps/backend/.env.prod"
MATERIALIZE_PRODUCTION_ENV_DRY_RUN_COMMAND = (
    "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error"
)
MATERIALIZE_PRODUCTION_ENV_WRITE_COMMAND = (
    "python scripts/materialize_production_env.py --overwrite --source-label sops:production --fail-on-error"
)
DECISION_QUALITY_ROUTE_AUDIT_COMMAND = (
    "cd apps/backend && python -m app.verification.decision_quality_live_runner "
    "--execute --allow-external-model-calls --repeats 3 "
    "--candidate-id <approved-candidate-id> --fail-on-error"
)
STRUCTURED_WORKFLOW_QUALITY_AUDIT_COMMAND = (
    "cd apps/backend && python -m app.verification.structured_workflow_quality_live_runner "
    "--execute --allow-external-model-calls --repeats 3 "
    "--candidate-id <approved-candidate-id> --fail-on-error"
)
INGESTION_QUALITY_OFFLINE_COMMAND = (
    "cd apps/backend && python -m app.verification.ingestion_quality_eval "
    "--mode offline --fail-on-issue"
)
INGESTION_QUALITY_ONLINE_COMMAND = (
    "cd apps/backend && python -m app.verification.ingestion_quality_eval "
    "--mode online --allow-external-model-calls --fail-on-issue"
)
SECRET_SHAPE_PATTERNS = (
    (re.compile(r"sk-or-v1-[A-Za-z0-9_-]+"), "[redacted-openrouter-key]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@"), "[redacted-credential-uri]"),
    (re.compile(r"ENC\[AES256_GCM,"), "[redacted-sops-ciphertext]"),
)
VERIFY_COMMAND_BY_REPORT = {
    "production_env_validation": "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
    "production_compose_config": (
        "python scripts/check_production_compose_config.py --env-file apps/backend/.env.prod --fail-on-error"
    ),
    "frontend_release_checks": "python scripts/run_frontend_release_checks.py --fail-on-error",
    "ingestion_quality_offline": INGESTION_QUALITY_OFFLINE_COMMAND,
    "ingestion_quality_online": INGESTION_QUALITY_ONLINE_COMMAND,
    "production_operator_preflight": (
        "python scripts/run_production_operator_preflight.py --env-file apps/backend/.env.prod --fail-on-blocked"
    ),
    "production_stack_release": "python scripts/run_production_stack_release.py --env-file apps/backend/.env.prod --fail-on-blocked",
    "release_gate": (
        "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod "
        "--backend-url [backend-url]"
    ),
    "secret_inventory_validation": (
        "python scripts/validate_secret_inventory.py "
        "--inventory docs/verification/release-gate-secret-inventory-latest.json "
        "--env-file apps/backend/.env.prod --fail-on-error"
    ),
}
SECTION_ORDER = (
    ("critical_secrets", "Critical Secrets", 10),
    ("database_runtime", "Database And Runtime Identity", 20),
    ("object_storage", "Object Storage", 30),
    ("observability", "Observability", 40),
    ("production_profile", "Production Profile", 50),
    ("release_verification", "Release Verification", 90),
)
SECTION_BY_KEY = {key: {"section_key": key, "section_label": label, "priority": priority} for key, label, priority in SECTION_ORDER}
CRITICAL_SECRET_KEYS = frozenset({"APP_API_KEY", "APP_SECRET_KEY", "OPENROUTER_API_KEY"})
DATABASE_RUNTIME_KEYS = frozenset({"DATABASE_URL", "DEPLOYMENT_CONFIG_ID", "POSTGRES_PASSWORD", "REDIS_URL"})
OBJECT_STORAGE_KEYS = frozenset(
    {
        "MINIO_ACCESS_KEY",
        "MINIO_BUCKET",
        "MINIO_ENDPOINT",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_SECRET_KEY",
    }
)
OBSERVABILITY_KEYS = frozenset({"LOG_FORMAT", "SENTRY_DSN"})
PRODUCTION_PROFILE_KEYS = frozenset({"APP_DEBUG", "APP_ENV", "MINIO_SECURE", "RATE_LIMIT_STORAGE_URI", "REDIS_ENABLED"})
OPERATOR_ACTION_CATEGORY_BY_KEY = {
    "runtime_profile": "production_profile",
    "secrets": "secrets",
    "object_storage": "object_storage",
    "observability": "observability",
    "rate_limit_storage": "production_profile",
    "production_dry_run": "production_profile",
}


REPORT_SPECS = (
    {
        "key": "production_env_validation",
        "label": "Production env validation",
        "filename": "production-env-validation-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "production_compose_config",
        "label": "Production compose config",
        "filename": "production-compose-config-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "public_api_leak_scan",
        "label": "Public API leak scan",
        "filename": "public-api-leak-scan-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "frontend_release_checks",
        "label": "Frontend release checks",
        "filename": "frontend-release-checks-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "decision_quality_live_audit",
        "label": "Decision quality live audit",
        "filename": "decision-quality-live-audit-latest.json",
        "accepted_statuses": frozenset({"passed"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "ingestion_quality_offline",
        "label": "Ingestion quality offline evaluation",
        "filename": "ingestion-quality-offline-latest.json",
        "accepted_statuses": frozenset({"passed"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "ingestion_quality_online",
        "label": "Ingestion quality online evaluation",
        "filename": "ingestion-quality-online-latest.json",
        "accepted_statuses": frozenset({"passed"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "structured_workflow_quality_live_audit",
        "label": "Structured workflow quality live audit",
        "filename": "structured-workflow-quality-live-audit-latest.json",
        "accepted_statuses": frozenset({"passed"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "production_preflight",
        "label": "Production preflight",
        "filename": "production-preflight-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset({"warn"}),
    },
    {
        "key": "production_operator_preflight",
        "label": "Production operator preflight",
        "filename": "production-operator-preflight-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "production_stack_release",
        "label": "Production stack release",
        "filename": "production-stack-release-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "release_gate",
        "label": "Release gate",
        "filename": "release-gate-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "release_gate_secret_inventory",
        "label": "Release gate secret inventory",
        "filename": "release-gate-secret-inventory-latest.json",
        "accepted_statuses": frozenset({"pass", "required"}),
        "warning_statuses": frozenset(),
    },
    {
        "key": "secret_inventory_validation",
        "label": "Secret inventory validation",
        "filename": "secret-inventory-validation-latest.json",
        "accepted_statuses": frozenset({"pass"}),
        "warning_statuses": frozenset(),
    },
)
CANDIDATE_ENV_FIELD_BY_REPORT = {
    "production_env_validation": "env_path",
    "production_compose_config": "env_file",
    "production_operator_preflight": "env_file",
    "production_stack_release": "env_file",
    "secret_inventory_validation": "env_path",
}


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


def _parse_report_time(report: dict[str, Any]) -> datetime | None:
    raw = report.get("generated_at") or report.get("exported_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale(report: dict[str, Any], *, now: datetime, max_age_hours: float) -> bool:
    report_time = _parse_report_time(report)
    if report_time is None:
        return True
    age_seconds = (now.astimezone(timezone.utc) - report_time).total_seconds()
    return age_seconds > max_age_hours * 3600


def _read_json_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _additional_condition_failed(spec: dict[str, Any], report: dict[str, Any]) -> bool:
    if spec["key"] == "production_env_validation":
        return report.get("release_allowed") is not True
    if spec["key"] == "production_compose_config":
        return report.get("compose_config_ok") is not True
    if spec["key"] == "public_api_leak_scan":
        return int(report.get("leak_count") or 0) != 0
    if spec["key"] == "frontend_release_checks":
        return report.get("release_allowed") is not True or int(report.get("failed_command_count") or 0) != 0
    if spec["key"] == "production_operator_preflight":
        return report.get("release_candidate_ready") is not True
    if spec["key"] == "production_stack_release":
        return report.get("release_candidate_ready") is not True or report.get("release_executed") is not True
    if spec["key"] == "release_gate":
        return report.get("release_allowed") is not True
    if spec["key"] == "secret_inventory_validation":
        return str(report.get("status") or "") != "pass"
    return False


def _normalize_project_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip()


def _candidate_env_mismatch(spec: dict[str, Any], report: dict[str, Any]) -> dict[str, Any] | None:
    report_key = str(spec["key"])
    field = CANDIDATE_ENV_FIELD_BY_REPORT.get(report_key)
    if not field:
        return None
    actual = _normalize_project_path(report.get(field))
    if not actual or actual == EXPECTED_PRODUCTION_ENV_FILE:
        return None
    command = VERIFY_COMMAND_BY_REPORT.get(report_key, "")
    return {
        "report_key": report_key,
        "report_label": str(spec["label"]),
        "field": field,
        "expected_env_file": EXPECTED_PRODUCTION_ENV_FILE,
        "actual_env_file": actual,
        "regenerate_command": command,
    }


def _candidate_env_mismatch_action(mismatch: dict[str, Any]) -> dict[str, Any]:
    regenerate_command = str(mismatch.get("regenerate_command") or "").strip()
    verification_commands = [
        MATERIALIZE_PRODUCTION_ENV_DRY_RUN_COMMAND,
        MATERIALIZE_PRODUCTION_ENV_WRITE_COMMAND,
    ]
    if regenerate_command:
        verification_commands.append(regenerate_command)
    return {
        "key": "candidate_env_file",
        "label": "Candidate production env file",
        "status": "fail",
        "category": "release_verification",
        "config_keys": [],
        "requires_secret_update": False,
        "production_required": True,
        "source_report": mismatch.get("report_key"),
        "action": (
            f"Regenerate {mismatch.get('report_label') or mismatch.get('report_key')} with "
            f"{mismatch.get('expected_env_file')}; current latest report uses "
            f"{mismatch.get('actual_env_file')}."
        ),
        "verification_commands": verification_commands,
    }


def _route_fingerprint(report: dict[str, Any], field: str) -> str:
    identity = report.get(field)
    if not isinstance(identity, dict):
        return ""
    return str(identity.get("fingerprint") or "").strip()


def _decision_quality_route_mismatch(
    spec: dict[str, Any],
    report: dict[str, Any],
    production_preflight: dict[str, Any],
) -> dict[str, Any] | None:
    if spec["key"] in {"ingestion_quality_offline", "ingestion_quality_online"}:
        quality_identity = report.get("parser_identity") if isinstance(report.get("parser_identity"), dict) else {}
        quality_fingerprint = str(
            quality_identity.get("fingerprint") or report.get("parser_fingerprint") or ""
        ).strip()
        runtime_fingerprint = _route_fingerprint(production_preflight, "document_parser_identity")
        if not quality_fingerprint:
            reason = "quality_report_parser_fingerprint_missing"
        elif not runtime_fingerprint:
            reason = "runtime_parser_fingerprint_missing"
        elif quality_fingerprint != runtime_fingerprint:
            reason = "quality_report_parser_fingerprint_mismatch"
        else:
            return None
        return {
            "report_key": str(spec["key"]),
            "report_label": str(spec["label"]),
            "reason": reason,
            "quality_route_fingerprint": quality_fingerprint or None,
            "runtime_route_fingerprint": runtime_fingerprint or None,
            "regenerate_command": (
                INGESTION_QUALITY_OFFLINE_COMMAND
                if spec["key"] == "ingestion_quality_offline"
                else INGESTION_QUALITY_ONLINE_COMMAND
            ),
        }
    if spec["key"] == "structured_workflow_quality_live_audit":
        identities = report.get("route_identities") if isinstance(report.get("route_identities"), dict) else {}
        for workflow, runtime_field in (
            ("briefing", "briefing_route_identity"),
            ("committee", "committee_route_identity"),
        ):
            quality_identity = identities.get(workflow) if isinstance(identities.get(workflow), dict) else {}
            quality_fingerprint = str(quality_identity.get("fingerprint") or "").strip()
            runtime_fingerprint = _route_fingerprint(production_preflight, runtime_field)
            if not quality_fingerprint:
                reason = f"quality_report_{workflow}_route_fingerprint_missing"
            elif not runtime_fingerprint:
                reason = f"runtime_{workflow}_route_fingerprint_missing"
            elif quality_fingerprint != runtime_fingerprint:
                reason = f"quality_report_{workflow}_route_fingerprint_mismatch"
            else:
                continue
            return {
                "report_key": str(spec["key"]),
                "report_label": str(spec["label"]),
                "workflow": workflow,
                "reason": reason,
                "quality_route_fingerprint": quality_fingerprint or None,
                "runtime_route_fingerprint": runtime_fingerprint or None,
                "regenerate_command": STRUCTURED_WORKFLOW_QUALITY_AUDIT_COMMAND,
            }
        return None
    if spec["key"] != "decision_quality_live_audit":
        return None
    quality_fingerprint = _route_fingerprint(report, "route_identity")
    runtime_fingerprint = _route_fingerprint(production_preflight, "decision_chat_route_identity")
    if not quality_fingerprint:
        reason = "quality_report_route_fingerprint_missing"
    elif not runtime_fingerprint:
        reason = "runtime_route_fingerprint_missing"
    elif quality_fingerprint != runtime_fingerprint:
        reason = "quality_report_route_fingerprint_mismatch"
    else:
        return None
    return {
        "report_key": str(spec["key"]),
        "report_label": str(spec["label"]),
        "reason": reason,
        "quality_route_fingerprint": quality_fingerprint or None,
        "runtime_route_fingerprint": runtime_fingerprint or None,
        "regenerate_command": DECISION_QUALITY_ROUTE_AUDIT_COMMAND,
    }


def _decision_quality_route_mismatch_action(mismatch: dict[str, Any]) -> dict[str, Any]:
    reason = str(mismatch.get("reason") or "route_identity_unverified")
    ingestion_quality = str(mismatch.get("report_key") or "").startswith("ingestion_quality_")
    if reason == "quality_report_parser_fingerprint_missing":
        detail = "The ingestion-quality report predates parser identity binding."
    elif reason == "runtime_parser_fingerprint_missing":
        detail = "The production preflight does not expose the current document-parser identity."
    elif reason == "quality_report_parser_fingerprint_mismatch":
        detail = "The ingestion-quality report was produced by a different parser implementation or configuration."
    elif reason == "quality_report_route_fingerprint_missing":
        detail = "The passing decision-quality report predates route identity binding."
    elif reason == "runtime_route_fingerprint_missing":
        detail = "The production preflight does not expose the current decision-chat route identity."
    else:
        detail = "The passing decision-quality report was produced by a different decision-chat route or implementation."
    structured = mismatch.get("report_key") == "structured_workflow_quality_live_audit"
    return {
        "key": (
            "ingestion_quality_parser_identity"
            if ingestion_quality
            else "structured_workflow_quality_route_identity"
            if structured
            else "decision_quality_route_identity"
        ),
        "label": (
            "Ingestion quality parser identity"
            if ingestion_quality
            else "Structured workflow quality route identity"
            if structured
            else "Decision quality route identity"
        ),
        "status": "fail",
        "category": "release_verification",
        "config_keys": [],
        "requires_secret_update": False,
        "production_required": True,
        "source_report": mismatch.get("report_key"),
        "action": (
            f"{detail} Regenerate the ingestion-quality evidence before release."
            if ingestion_quality
            else f"{detail} Run an approved repeated live audit against the current route before release."
        ),
        "verification_commands": [
            str(mismatch.get("regenerate_command") or "")
            if ingestion_quality
            else STRUCTURED_WORKFLOW_QUALITY_AUDIT_COMMAND
            if structured
            else DECISION_QUALITY_ROUTE_AUDIT_COMMAND
        ],
    }


def _next_actions_from_report(spec: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for field in ("next_actions", "required_actions"):
        value = report.get(field)
        if isinstance(value, list):
            candidates.extend(item for item in value if isinstance(item, dict))
    actions: list[dict[str, Any]] = []
    for item in candidates:
        actions.extend(_expand_multi_key_action({"source_report": spec["key"], **item}))
    return actions


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _operator_action_category(action: dict[str, Any]) -> str:
    key = str(action.get("key") or "").strip()
    return OPERATOR_ACTION_CATEGORY_BY_KEY.get(key, "")


def _expand_multi_key_action(action: dict[str, Any]) -> list[dict[str, Any]]:
    config_keys = sorted(set(_string_list(action.get("config_keys"))))
    if len(config_keys) <= 1 or _operator_action_category(action):
        return [action]
    expanded: list[dict[str, Any]] = []
    for config_key in config_keys:
        child = dict(action)
        child["config_keys"] = [config_key]
        child["split_config_key"] = config_key
        child["split_from_config_keys"] = config_keys
        child["split_from_action_key"] = action.get("key")
        expanded.append(child)
    return expanded


def _action_group_key(action: dict[str, Any]) -> tuple[str, str, list[str]]:
    operator_category = _operator_action_category(action)
    if operator_category:
        key = str(action.get("key") or operator_category)
        label = str(action.get("label") or key)
        return f"action:{key}", label, []
    config_keys = sorted(set(_string_list(action.get("config_keys"))))
    if config_keys:
        label = ", ".join(config_keys)
        return f"config:{','.join(config_keys)}", label, config_keys
    key = str(action.get("key") or action.get("category") or "unknown_action")
    label = str(action.get("label") or key)
    return f"action:{key}", label, []


def _section_for_group(config_keys: list[str], categories: list[str]) -> dict[str, Any]:
    key_set = set(config_keys)
    category_set = set(categories)
    if key_set & CRITICAL_SECRET_KEYS or "secrets" in category_set:
        return SECTION_BY_KEY["critical_secrets"]
    if key_set & DATABASE_RUNTIME_KEYS or "database" in category_set or "compose_database_secret" in category_set:
        return SECTION_BY_KEY["database_runtime"]
    if key_set & OBJECT_STORAGE_KEYS or "object_storage" in category_set or "compose_deployment_secret" in category_set:
        return SECTION_BY_KEY["object_storage"]
    if key_set & OBSERVABILITY_KEYS or "observability" in category_set:
        return SECTION_BY_KEY["observability"]
    if key_set & PRODUCTION_PROFILE_KEYS or "production_profile" in category_set:
        return SECTION_BY_KEY["production_profile"]
    return SECTION_BY_KEY["release_verification"]


def _group_next_actions(next_actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for action in next_actions:
        group_key, label, config_keys = _action_group_key(action)
        operator_category = _operator_action_category(action)
        group = groups.setdefault(
            group_key,
            {
                "group_key": group_key,
                "label": label,
                "config_keys": config_keys,
                "categories": [],
                "source_reports": [],
                "action_count": 0,
                "referenced_config_keys": [],
                "requires_secret_update": False,
                "production_required": False,
                "split_from_multi_key_action_count": 0,
                "split_from_config_keys": [],
                "verification_commands": [],
                "sample_actions": [],
            },
        )
        group["action_count"] += 1
        _append_unique(group["categories"], _string_list(action.get("category")))
        _append_unique(group["referenced_config_keys"], _string_list(action.get("config_keys")))
        if operator_category:
            _append_unique(group["categories"], [operator_category])
        source_report = str(action.get("source_report") or "")
        if source_report:
            _append_unique(group["source_reports"], [source_report])
            if action.get("requires_secret_update") is True or action.get("key") in {
                "candidate_env_file",
                "env_file",
                "production_compose_config",
            }:
                _append_unique(group["verification_commands"], [MATERIALIZE_PRODUCTION_ENV_DRY_RUN_COMMAND])
                _append_unique(group["verification_commands"], [MATERIALIZE_PRODUCTION_ENV_WRITE_COMMAND])
            if action.get("key") == "candidate_env_file":
                _append_unique(group["verification_commands"], _string_list(action.get("verification_commands")))
            command = VERIFY_COMMAND_BY_REPORT.get(source_report)
            if command:
                _append_unique(group["verification_commands"], [command])
        if action.get("requires_secret_update") is True:
            group["requires_secret_update"] = True
        if action.get("production_required") is True:
            group["production_required"] = True
        split_keys = action.get("split_from_config_keys")
        if isinstance(split_keys, list) and split_keys:
            split_key_values = [str(item) for item in split_keys]
            group["split_from_multi_key_action_count"] += 1
            if split_key_values not in group["split_from_config_keys"]:
                group["split_from_config_keys"].append(split_key_values)
        if len(group["sample_actions"]) < 3:
            group["sample_actions"].append(
                {
                    "key": action.get("key"),
                    "status": action.get("status"),
                    "action": action.get("action"),
                    "source_report": action.get("source_report"),
                    "split_config_key": action.get("split_config_key"),
                    "split_from_config_keys": action.get("split_from_config_keys"),
                }
            )
    for group in groups.values():
        group["categories"] = sorted(group["categories"])
        group["referenced_config_keys"] = sorted(group["referenced_config_keys"])
        section = _section_for_group(group["config_keys"], group["categories"])
        group["section_key"] = section["section_key"]
        group["section_label"] = section["section_label"]
        group["priority"] = section["priority"]
    return sorted(groups.values(), key=lambda group: (group["priority"], str(group["label"]), str(group["group_key"])))


def _render_action_plan_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Production Readiness Action Plan",
        "",
        f"- Generated at: `{report.get('generated_at', '')}`",
        f"- Status: `{report.get('status', '')}`",
        f"- Readiness: `{report.get('readiness_state', '')}`",
        f"- Blocking reports: `{', '.join(report.get('blocking_reports', [])) or 'none'}`",
        f"- Missing reports: `{', '.join(report.get('missing_reports', [])) or 'none'}`",
        f"- Stale reports: `{', '.join(report.get('stale_reports', [])) or 'none'}`",
        f"- Route identity mismatches: `{report.get('route_identity_mismatch_count', 0)}`",
        f"- Action groups: `{report.get('action_group_count', 0)}`",
        "",
    ]
    action_groups = report.get("action_groups", [])
    top_remediation = report.get("top_remediation", [])
    lines.extend(["## Next Remediation Summary", ""])
    if top_remediation:
        lines.append(
            f"Showing `{len(top_remediation)}` of `{report.get('action_group_count', 0)}` action groups"
        )
        lines.append("")
        for item in top_remediation:
            label = str(item.get("label") or item.get("group_key") or "unknown")
            section_label = str(item.get("section_label") or "Release Verification")
            lines.append(f"- [ ] `{label}` (`{section_label}`)")
            lines.append(f"  - Group: `{item.get('group_key', '')}`")
            lines.append(f"  - Priority: `{item.get('priority', '')}`")
            commands = item.get("verification_commands", [])
            if commands:
                lines.append(f"  - First verify: `{commands[0]}`")
        lines.append("")
    else:
        lines.extend(["No blocking remediation items.", ""])

    if not action_groups:
        lines.extend(["## Checklist", "", "No blocking action groups.", ""])
        return "\n".join(lines)

    lines.extend(["## Checklist", ""])
    current_section = ""
    section_index = 0
    for group in action_groups:
        section_label = str(group.get("section_label") or "Release Verification")
        if section_label != current_section:
            current_section = section_label
            section_index += 1
            lines.extend(["", f"## {section_index}. {section_label}", ""])
        label = str(group.get("label") or group.get("group_key") or "unknown")
        lines.append(f"- [ ] `{label}`")
        lines.append(f"  - Group: `{group.get('group_key', '')}`")
        lines.append(f"  - Priority: `{group.get('priority', '')}`")
        lines.append(f"  - Categories: {', '.join(group.get('categories', [])) or 'none'}")
        lines.append(f"  - Sources: {', '.join(group.get('source_reports', [])) or 'none'}")
        lines.append(f"  - Action count: `{group.get('action_count', 0)}`")
        sample_actions = group.get("sample_actions", [])
        if isinstance(sample_actions, list) and sample_actions:
            first_action = sample_actions[0] if isinstance(sample_actions[0], dict) else {}
            action_text = str(first_action.get("action") or "").strip()
            if action_text:
                lines.append(f"  - Action: {action_text}")
        if group.get("requires_secret_update"):
            lines.append("  - Secret update: required")
        commands = group.get("verification_commands", [])
        if commands:
            lines.append("  - Verify:")
            for command in commands:
                lines.append(f"    - `{command}`")
    lines.append("")
    return "\n".join(lines)


def _digest_blocker(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_key": group.get("group_key"),
        "label": group.get("label"),
        "section_key": group.get("section_key"),
        "section_label": group.get("section_label"),
        "priority": group.get("priority"),
        "config_keys": group.get("config_keys", []),
        "referenced_config_keys": group.get("referenced_config_keys", []),
        "categories": group.get("categories", []),
        "source_reports": group.get("source_reports", []),
        "requires_secret_update": group.get("requires_secret_update") is True,
        "production_required": group.get("production_required") is True,
        "split_from_multi_key_action_count": int(group.get("split_from_multi_key_action_count") or 0),
        "verification_commands": group.get("verification_commands", []),
    }


def _build_readiness_digest(report: dict[str, Any]) -> dict[str, Any]:
    blockers = [_digest_blocker(group) for group in report.get("top_remediation", [])]
    blocker_total_count = int(report.get("action_group_count") or 0)
    blocker_shown_count = len(blockers)
    return {
        "report_type": "production_readiness_digest",
        "status": report.get("status"),
        "readiness_state": report.get("readiness_state"),
        "release_allowed": report.get("release_allowed") is True,
        "generated_at": report.get("generated_at"),
        "summary_latest_path": report.get("latest_path"),
        "action_plan_latest_path": report.get("action_plan_latest_path"),
        "latest_path": report.get("digest_latest_path"),
        "blocker_count": blocker_total_count,
        "blocker_total_count": blocker_total_count,
        "blocker_shown_count": blocker_shown_count,
        "blocking_report_count": int(report.get("blocking_report_count") or 0),
        "blocking_reports": report.get("blocking_reports", []),
        "missing_report_count": int(report.get("missing_report_count") or 0),
        "missing_reports": report.get("missing_reports", []),
        "stale_report_count": int(report.get("stale_report_count") or 0),
        "stale_reports": report.get("stale_reports", []),
        "candidate_env_mismatch_count": int(report.get("candidate_env_mismatch_count") or 0),
        "candidate_env_mismatches": report.get("candidate_env_mismatches", []),
        "route_identity_mismatch_count": int(report.get("route_identity_mismatch_count") or 0),
        "route_identity_mismatches": report.get("route_identity_mismatches", []),
        "warning_report_count": int(report.get("warning_report_count") or 0),
        "warning_reports": report.get("warning_reports", []),
        "top_remediation_limit": int(report.get("top_remediation_limit") or 0),
        "top_remediation_count": len(blockers),
        "blockers": blockers,
    }


def _write_report(
    report: dict[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    project_root: Path,
) -> dict[str, Any]:
    export_path = out_dir / f"production-readiness-summary-{stamp}.json"
    latest_path = out_dir / "production-readiness-summary-latest.json"
    action_plan_path = out_dir / f"production-readiness-action-plan-{stamp}.md"
    action_plan_latest_path = out_dir / "production-readiness-action-plan-latest.md"
    digest_path = out_dir / f"production-readiness-digest-{stamp}.json"
    digest_latest_path = out_dir / "production-readiness-digest-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    report["action_plan_path"] = str(action_plan_path)
    report["action_plan_latest_path"] = str(action_plan_latest_path)
    report["digest_path"] = str(digest_path)
    report["digest_latest_path"] = str(digest_latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    public_report = json.loads(text)
    action_plan_text = _render_action_plan_markdown(public_report)
    action_plan_path.write_text(action_plan_text, encoding="utf-8", newline="\n")
    action_plan_latest_path.write_text(action_plan_text, encoding="utf-8", newline="\n")
    digest_text = json.dumps(_build_readiness_digest(public_report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    digest_path.write_text(digest_text, encoding="utf-8", newline="\n")
    digest_latest_path.write_text(digest_text, encoding="utf-8", newline="\n")
    return json.loads(text)


def summarize_production_readiness(
    *,
    report_dir: Path | str | None = None,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
    top_remediation_limit: int = 10,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    source_dir = Path(report_dir) if report_dir is not None else DEFAULT_REPORT_DIR
    destination = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    if not source_dir.is_absolute():
        source_dir = root / source_dir
    if not destination.is_absolute():
        destination = root / destination
    source_dir = _ensure_inside_project(source_dir, root)
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")

    reports: list[dict[str, Any]] = []
    missing_reports: list[str] = []
    stale_reports: list[str] = []
    warning_reports: list[str] = []
    blocking_reports: list[str] = []
    candidate_env_mismatches: list[dict[str, Any]] = []
    route_identity_mismatches: list[dict[str, Any]] = []
    next_actions: list[dict[str, Any]] = []

    production_preflight_report: dict[str, Any] = {}
    production_preflight_path = source_dir / "production-preflight-latest.json"
    if production_preflight_path.exists():
        try:
            production_preflight_report = _read_json_report(production_preflight_path)
        except (json.JSONDecodeError, OSError):
            production_preflight_report = {}

    for spec in REPORT_SPECS:
        path = source_dir / str(spec["filename"])
        if not path.exists():
            missing_reports.append(str(spec["key"]))
            reports.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "filename": spec["filename"],
                    "status": "missing",
                    "blocking": True,
                    "stale": False,
                }
            )
            continue

        try:
            report = _read_json_report(path)
            report_status = str(report.get("status") or "unknown")
            stale = _is_stale(report, now=timestamp, max_age_hours=max_age_hours)
            condition_failed = _additional_condition_failed(spec, report)
            candidate_env_mismatch = _candidate_env_mismatch(spec, report)
            route_identity_mismatch = _decision_quality_route_mismatch(spec, report, production_preflight_report)
            warning = report_status in spec["warning_statuses"]
            blocking = stale or condition_failed or candidate_env_mismatch is not None or route_identity_mismatch is not None or (
                report_status not in spec["accepted_statuses"] and report_status not in spec["warning_statuses"]
            )
            if stale:
                stale_reports.append(str(spec["key"]))
            if candidate_env_mismatch is not None:
                candidate_env_mismatches.append(candidate_env_mismatch)
            if route_identity_mismatch is not None:
                route_identity_mismatches.append(route_identity_mismatch)
            if warning and not blocking:
                warning_reports.append(str(spec["key"]))
            if blocking:
                blocking_reports.append(str(spec["key"]))
                next_actions.extend(_next_actions_from_report(spec, report))
                if candidate_env_mismatch is not None:
                    next_actions.append(_candidate_env_mismatch_action(candidate_env_mismatch))
                if route_identity_mismatch is not None:
                    next_actions.append(_decision_quality_route_mismatch_action(route_identity_mismatch))
            reports.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "filename": spec["filename"],
                    "report_type": report.get("report_type"),
                    "status": report_status,
                    "blocking": blocking,
                    "warning": warning,
                    "stale": stale,
                    "generated_at": report.get("generated_at") or report.get("exported_at"),
                    "blocking_steps": report.get("blocking_steps", []),
                    "next_action_count": len(_next_actions_from_report(spec, report)),
                    "candidate_env_mismatch": candidate_env_mismatch,
                    "route_identity_mismatch": route_identity_mismatch,
                }
            )
        except Exception as exc:
            blocking_reports.append(str(spec["key"]))
            reports.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "filename": spec["filename"],
                    "status": "unreadable",
                    "blocking": True,
                    "stale": False,
                    "exception_type": type(exc).__name__,
                }
            )

    status = "pass"
    readiness_state = "release_ready"
    if missing_reports or stale_reports or blocking_reports:
        status = "blocked"
        readiness_state = "blocked"
    elif warning_reports:
        status = "warn"
        readiness_state = "candidate_ready_with_warnings"

    action_groups = _group_next_actions(next_actions)
    safe_top_limit = max(0, int(top_remediation_limit))
    top_remediation = action_groups[:safe_top_limit]
    report = {
        "report_type": "production_readiness_summary",
        "status": status,
        "readiness_state": readiness_state,
        "release_allowed": status == "pass",
        "generated_at": timestamp.isoformat(),
        "report_dir": str(source_dir),
        "max_age_hours": max_age_hours,
        "report_count": len(reports),
        "blocking_report_count": len(blocking_reports),
        "blocking_reports": blocking_reports,
        "missing_report_count": len(missing_reports),
        "missing_reports": missing_reports,
        "stale_report_count": len(stale_reports),
        "stale_reports": stale_reports,
        "candidate_env_mismatch_count": len(candidate_env_mismatches),
        "candidate_env_mismatches": candidate_env_mismatches,
        "route_identity_mismatch_count": len(route_identity_mismatches),
        "route_identity_mismatches": route_identity_mismatches,
        "warning_report_count": len(warning_reports),
        "warning_reports": warning_reports,
        "next_action_count": len(next_actions),
        "next_actions": next_actions,
        "action_group_count": len(action_groups),
        "action_groups": action_groups,
        "top_remediation_limit": safe_top_limit,
        "top_remediation_count": len(top_remediation),
        "top_remediation": top_remediation,
        "reports": reports,
        "policy_basis": [
            "Production readiness must be derived from latest release artifacts, not memory or ad hoc checks.",
            "Missing, stale, failed, blocked, or unreadable required reports block release readiness.",
            "Frontend npm audit, lint, type-check, and production build must be represented by the latest frontend release checks artifact.",
            "The passing decision-quality report must be bound to the current decision-chat route and implementation fingerprint.",
            "The summary report and action plan store relative paths and redact common secret shapes.",
        ],
    }
    return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)


def summary_exit_code(result: dict[str, Any], *, fail_on_blocked: bool) -> int:
    if not fail_on_blocked:
        return 0
    return 0 if result.get("status") == "pass" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize latest production readiness reports into one safe artifact.")
    parser.add_argument("--report-dir", default="docs/verification")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument("--top-remediation-limit", type=int, default=10)
    parser.add_argument("--fail-on-blocked", action="store_true")
    args = parser.parse_args()

    result = summarize_production_readiness(
        report_dir=Path(args.report_dir),
        out_dir=Path(args.out_dir) if args.out_dir else None,
        max_age_hours=args.max_age_hours,
        top_remediation_limit=args.top_remediation_limit,
    )
    print(format_cli_json(result))
    return summary_exit_code(result, fail_on_blocked=args.fail_on_blocked)


if __name__ == "__main__":
    raise SystemExit(main())
