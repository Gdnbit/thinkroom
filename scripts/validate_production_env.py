from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_origin
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BACKEND_APP = PROJECT_ROOT / "apps" / "backend"
if str(BACKEND_APP) not in sys.path:
    sys.path.insert(0, str(BACKEND_APP))

from app.config import Settings, production_config_errors  # noqa: E402
from app.utils.secret_source_policy import (  # noqa: E402
    ACCEPTED_SECRET_SOURCE_PREFIXES,
    source_family as _source_family,
    trusted_secret_source as _trusted_source_provenance,
)


PLACEHOLDER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"请替换",
        r"替换为",
        r"<[^>]+>",
        r"your[-_ ]",
        r"change[-_ ]?this",
        r"placeholder",
        r"password",
        r"from-secret-store",
    ]
]

REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS = (
    "DATABASE_URL",
    "APP_API_KEY",
    "APP_SECRET_KEY",
    "OPENROUTER_API_KEY",
    "REDIS_URL",
    "RATE_LIMIT_STORAGE_URI",
    "MINIO_ENDPOINT",
    "MINIO_BUCKET",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "LOG_FORMAT",
    "SENTRY_DSN",
)
OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS = (
    "EMBEDDING_API_KEY",
    "COINGECKO_API_KEY",
    "FRED_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "TAVILY_API_KEY",
    "GOOGLE_AI_API_KEY",
    "TWITTER_COOKIE",
    "WEIBO_COOKIE",
    "XHS_COOKIE",
    "DOUYIN_COOKIE",
    "WEMPRSS_API_TOKEN",
)
COMPOSE_DEPLOYMENT_CHECKS: list[dict[str, Any]] = [
    {
        "key": "POSTGRES_DB",
        "category": "compose_deployment_config",
        "required_for_production": False,
        "min_length": 1,
        "remediation": "Set POSTGRES_DB to the production database name, or remove it if your deployment does not use docker-compose.prod.yml interpolation.",
    },
    {
        "key": "POSTGRES_USER",
        "category": "compose_deployment_config",
        "required_for_production": False,
        "min_length": 1,
        "remediation": "Set POSTGRES_USER to the production database user, or remove it if your deployment does not use docker-compose.prod.yml interpolation.",
    },
    {
        "key": "POSTGRES_PASSWORD",
        "category": "compose_deployment_secret",
        "required_for_production": False,
        "min_length": 16,
        "remediation": "Inject POSTGRES_PASSWORD from SOPS or the deployment secret store when docker-compose.prod.yml is used.",
    },
    {
        "key": "MINIO_ROOT_USER",
        "category": "compose_deployment_secret",
        "required_for_production": False,
        "min_length": 8,
        "default_values": ["minioadmin"],
        "remediation": "Inject MINIO_ROOT_USER from SOPS or the deployment secret store when docker-compose.prod.yml is used.",
    },
    {
        "key": "MINIO_ROOT_PASSWORD",
        "category": "compose_deployment_secret",
        "required_for_production": False,
        "min_length": 16,
        "default_values": ["minioadmin"],
        "remediation": "Inject MINIO_ROOT_PASSWORD from SOPS or the deployment secret store when docker-compose.prod.yml is used.",
    },
]
COMPOSE_DEPLOYMENT_KEYS = tuple(item["key"] for item in COMPOSE_DEPLOYMENT_CHECKS)
SOURCE_PROVENANCE_CONFIG_KEYS = (
    *REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS,
    *OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS,
)
SOURCE_METADATA_KEYS = {f"{key}_SOURCE" for key in SOURCE_PROVENANCE_CONFIG_KEYS}
ALLOWED_METADATA_KEYS = {"SECRETS_SOURCE", *SOURCE_METADATA_KEYS}


def _source_metadata_keys() -> list[str]:
    return [f"{key}_SOURCE" for key in SOURCE_PROVENANCE_CONFIG_KEYS]


def _active_source_provenance_config_keys(values: dict[str, str]) -> list[str]:
    keys = list(REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS)
    for key in OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS:
        if str(values.get(key, "") or "").strip():
            keys.append(key)
    return keys

PRODUCTION_CHECKS: list[dict[str, Any]] = [
    {
        "key": "APP_ENV",
        "category": "runtime_profile",
        "required_for_production": True,
        "expected": "production",
        "remediation": "Set APP_ENV=production for the deployment profile being validated.",
    },
    {
        "key": "DEPLOYMENT_CONFIG_ID",
        "category": "configuration",
        "required_for_production": True,
        "min_length": 8,
        "remediation": "Set DEPLOYMENT_CONFIG_ID to a non-secret release/config identifier and expose the same value from the running backend.",
    },
    {
        "key": "APP_DEBUG",
        "category": "runtime_profile",
        "required_for_production": True,
        "expected": "false",
        "remediation": "Set APP_DEBUG=false before production release.",
    },
    {
        "key": "DATABASE_URL",
        "category": "database",
        "required_for_production": True,
        "uri_metadata": True,
        "forbidden_prefixes": ["sqlite"],
        "remediation": "Use PostgreSQL/Postgres+pgvector for production; do not deploy with SQLite.",
    },
    {
        "key": "APP_API_KEY",
        "category": "secrets",
        "required_for_production": True,
        "min_length": 24,
        "remediation": "Inject a strong APP_API_KEY from SOPS or the deployment secret store.",
    },
    {
        "key": "APP_SECRET_KEY",
        "category": "secrets",
        "required_for_production": True,
        "min_length": 32,
        "default_values": ["dev-secret-key"],
        "remediation": "Inject a non-default APP_SECRET_KEY with at least 32 characters.",
    },
    {
        "key": "OPENROUTER_API_KEY",
        "category": "secrets",
        "required_for_production": True,
        "min_length": 16,
        "remediation": "Inject OPENROUTER_API_KEY from SOPS or the deployment secret store.",
    },
    {
        "key": "OPENROUTER_MOCK",
        "category": "runtime_profile",
        "required_for_production": True,
        "expected": "false",
        "remediation": "Set OPENROUTER_MOCK=false in production.",
    },
    {
        "key": "REDIS_ENABLED",
        "category": "shared_state",
        "required_for_production": True,
        "expected": "true",
        "remediation": "Set REDIS_ENABLED=true and point REDIS_URL to shared Redis.",
    },
    {
        "key": "REDIS_URL",
        "category": "shared_state",
        "required_for_production": True,
        "uri_metadata": True,
        "remediation": "Set REDIS_URL to the production Redis endpoint.",
    },
    {
        "key": "RATE_LIMIT_STORAGE_URI",
        "category": "shared_state",
        "required_for_production": True,
        "uri_metadata": True,
        "default_values": ["memory://"],
        "forbidden_prefixes": ["memory://"],
        "remediation": "Set RATE_LIMIT_STORAGE_URI to shared storage, normally Redis.",
    },
    {
        "key": "MINIO_ENDPOINT",
        "category": "object_storage",
        "required_for_production": True,
        "default_values": ["localhost:9000", "127.0.0.1:9000"],
        "remediation": "Set MINIO_ENDPOINT to the production object storage endpoint.",
    },
    {
        "key": "MINIO_SECURE",
        "category": "object_storage",
        "required_for_production": True,
        "expected": "true",
        "remediation": "Set MINIO_SECURE=true unless a documented risk exception is approved.",
    },
    {
        "key": "MINIO_ACCESS_KEY",
        "category": "object_storage",
        "required_for_production": True,
        "default_values": ["minioadmin"],
        "remediation": "Inject a non-default MINIO_ACCESS_KEY from SOPS or the deployment secret store.",
    },
    {
        "key": "MINIO_SECRET_KEY",
        "category": "object_storage",
        "required_for_production": True,
        "default_values": ["minioadmin"],
        "remediation": "Inject a non-default MINIO_SECRET_KEY from SOPS or the deployment secret store.",
    },
    {
        "key": "MINIO_BUCKET",
        "category": "object_storage",
        "required_for_production": True,
        "remediation": "Set MINIO_BUCKET to the production bucket name.",
    },
    {
        "key": "LOG_FORMAT",
        "category": "observability",
        "required_for_production": True,
        "expected": "json",
        "remediation": "Set LOG_FORMAT=json so production logs are machine parseable.",
    },
    {
        "key": "SENTRY_DSN",
        "category": "observability",
        "required_for_production": True,
        "remediation": "Set SENTRY_DSN or explicitly document an alternative error-tracking system.",
    },
]


def project_root_from_script() -> Path:
    return PROJECT_ROOT


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


def parse_env_file_with_diagnostics(
    path: Path | str,
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, int]]:
    values: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    first_key_lines: dict[str, int] = {}
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            issues.append({"line_number": line_number, "issue": "missing_equals"})
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            issues.append({"line_number": line_number, "issue": "empty_key"})
            continue
        if key in first_key_lines:
            issues.append(
                {
                    "line_number": line_number,
                    "first_line_number": first_key_lines[key],
                    "issue": "duplicate_key",
                    "key": key,
                }
            )
        else:
            first_key_lines[key] = line_number
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values, issues, first_key_lines


def parse_env_file(path: Path | str) -> dict[str, str]:
    values, _issues, _key_line_numbers = parse_env_file_with_diagnostics(path)
    return values


def _placeholder_like(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in PLACEHOLDER_PATTERNS)


def _boolish(value: str) -> str:
    return (value or "").strip().lower()


def _uri_metadata(value: str) -> dict[str, Any]:
    parsed = urlparse((value or "").strip())
    return {
        "scheme": parsed.scheme,
        "host_set": bool(parsed.hostname),
        "port_set": parsed.port is not None,
        "contains_credentials": bool(parsed.username or parsed.password),
    }


def _source_metadata_check(
    *,
    key: str,
    value: str,
    required: bool,
    target_config_key: str | None = None,
    target_config_keys: list[str] | None = None,
    stale_optional_source_metadata: bool = False,
) -> dict[str, Any]:
    text = (value or "").strip()
    set_ok = bool(text)
    placeholder_like = _placeholder_like(text)
    trusted = _trusted_source_provenance(text)
    status = (
        "fail"
        if (required and not set_ok)
        or placeholder_like
        or (set_ok and not trusted)
        or stale_optional_source_metadata
        else "pass"
    )
    source_marker_family = _source_family(text) if trusted else ("untrusted" if set_ok else "")
    if key == "SECRETS_SOURCE":
        remediation = (
            "Set SECRETS_SOURCE to a controlled secret source, or provide trusted per-key SOURCE metadata "
            "for every required secret-bearing production config."
        )
    elif stale_optional_source_metadata:
        remediation = (
            f"Remove stale {key}, or set {target_config_key} if that optional integration is intentionally enabled."
        )
    else:
        remediation = f"Set {key} to a controlled secret source for {target_config_key or key}."
    item: dict[str, Any] = {
        "key": key,
        "category": "secret_provenance",
        "status": status,
        "set": set_ok,
        "placeholder_like": placeholder_like,
        "default_like": False,
        "too_short": False,
        "expected": None,
        "matches_expected": trusted,
        "forbidden_value_shape": False,
        "required_for_production": required,
        "remediation": remediation if status == "fail" else "",
        "source_marker_present": set_ok,
        "source_marker_key": key,
        "source_marker_family": source_marker_family,
        "source_provenance_ok": trusted and not placeholder_like,
        "source_provenance_required": required,
        "config_keys": [key],
        **({"target_config_key": target_config_key} if target_config_key else {}),
        **(
            {
                "issue_code": "stale_optional_source_metadata",
                "alternative_config_keys": [target_config_key],
            }
            if stale_optional_source_metadata and target_config_key
            else {}
        ),
    }
    if key == "SECRETS_SOURCE":
        active_target_config_keys = list(target_config_keys or REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS)
        item.update(
            {
                "source_provenance_mode": "global_or_per_key",
                "alternative_config_keys": [
                    f"{config_key}_SOURCE" for config_key in active_target_config_keys
                ],
                "source_provenance_target_config_keys": active_target_config_keys,
            }
        )
    return item


def _source_metadata_checks(values: dict[str, str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    active_config_keys = _active_source_provenance_config_keys(values)
    per_key_complete = all(
        _trusted_source_provenance(values.get(f"{key}_SOURCE", ""))
        and not _placeholder_like(values.get(f"{key}_SOURCE", ""))
        for key in active_config_keys
    )
    global_source = values.get("SECRETS_SOURCE", "")
    if global_source or not per_key_complete:
        checks.append(
            _source_metadata_check(
                key="SECRETS_SOURCE",
                value=global_source,
                required=True,
                target_config_keys=active_config_keys,
            )
        )
    for config_key in SOURCE_PROVENANCE_CONFIG_KEYS:
        metadata_key = f"{config_key}_SOURCE"
        if metadata_key not in values:
            continue
        stale_optional_source_metadata = (
            config_key in OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS
            and not str(values.get(config_key, "") or "").strip()
        )
        checks.append(
            _source_metadata_check(
                key=metadata_key,
                value=values.get(metadata_key, ""),
                required=True,
                target_config_key=config_key,
                stale_optional_source_metadata=stale_optional_source_metadata,
            )
        )
    return checks


def _optional_sensitive_value_checks(values: dict[str, str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for key in OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS:
        text = str(values.get(key, "") or "").strip()
        if not text:
            continue
        placeholder_like = _placeholder_like(text)
        status = "fail" if placeholder_like else "pass"
        checks.append(
            {
                "key": key,
                "category": "optional_sensitive_config",
                "status": status,
                "set": True,
                "placeholder_like": placeholder_like,
                "default_like": False,
                "too_short": False,
                "expected": None,
                "matches_expected": True,
                "forbidden_value_shape": False,
                "required_for_production": False,
                "remediation": (
                    f"Set {key} to a real value from SOPS or the deployment secret store, "
                    "or remove it if the optional integration is disabled."
                )
                if status == "fail"
                else "",
                "config_keys": [key],
                **({"issue_code": "invalid_optional_sensitive_value"} if status == "fail" else {}),
            }
        )
    return checks


def _compose_deployment_value_checks(values: dict[str, str]) -> list[dict[str, Any]]:
    return [
        _check_item(values, spec)
        for spec in COMPOSE_DEPLOYMENT_CHECKS
        if str(values.get(str(spec["key"]), "") or "").strip()
    ]


def _check_item(values: dict[str, str], spec: dict[str, Any]) -> dict[str, Any]:
    key = spec["key"]
    raw = values.get(key, "")
    text = (raw or "").strip()
    set_ok = bool(text)
    placeholder_like = _placeholder_like(text)
    default_like = text in set(spec.get("default_values", []))
    min_length = int(spec.get("min_length", 1))
    too_short = set_ok and len(text) < min_length
    expected = spec.get("expected")
    expected_ok = True
    if expected is not None:
        expected_ok = _boolish(text) == str(expected).lower()
    forbidden_prefixes = [str(item).lower() for item in spec.get("forbidden_prefixes", [])]
    forbidden = any(text.lower().startswith(prefix) for prefix in forbidden_prefixes)
    missing = spec.get("required_for_production") and not set_ok
    source_provenance_metadata = bool(spec.get("source_provenance_metadata"))
    source_provenance_ok = True
    source_marker_family = ""
    if source_provenance_metadata:
        source_provenance_ok = _trusted_source_provenance(text)
        source_marker_family = _source_family(text) if source_provenance_ok else ("untrusted" if set_ok else "")
    failed = bool(
        missing
        or placeholder_like
        or default_like
        or too_short
        or not expected_ok
        or forbidden
        or (source_provenance_metadata and not source_provenance_ok)
    )
    item = {
        "key": key,
        "category": spec.get("category", "configuration"),
        "status": "fail" if failed else "pass",
        "set": set_ok,
        "placeholder_like": placeholder_like,
        "default_like": default_like,
        "too_short": too_short,
        "expected": expected if expected is not None else None,
        "matches_expected": expected_ok,
        "forbidden_value_shape": forbidden,
        "required_for_production": bool(spec.get("required_for_production")),
        "remediation": spec.get("remediation", "") if failed else "",
    }
    if source_provenance_metadata:
        item.update(
            {
                "source_marker_present": set_ok,
                "source_marker_key": key,
                "source_marker_family": source_marker_family,
                "source_provenance_ok": source_provenance_ok,
                "source_provenance_required": True,
            }
        )
    if spec.get("uri_metadata"):
        item["uri"] = _uri_metadata(text)
    return item


def _settings_from_values(values: dict[str, str]) -> Settings:
    class EnvFileOnlySettings(Settings):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        ):
            return (init_settings,)

    data: dict[str, Any] = {}
    for key, value in values.items():
        if key not in Settings.model_fields:
            continue
        annotation = Settings.model_fields[key].annotation
        if get_origin(annotation) is list and isinstance(value, str):
            try:
                data[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        data[key] = value
    return EnvFileOnlySettings(**data)


def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pass": sum(1 for item in checks if item["status"] == "pass"),
        "fail": sum(1 for item in checks if item["status"] == "fail"),
        "total": len(checks),
    }


def _env_file_issue_codes(checks: list[dict[str, Any]]) -> list[str]:
    codes: list[str] = []
    for item in checks:
        key = str(item.get("key", ""))
        code = str(item.get("issue_code", "")).strip()
        if item.get("status") != "fail" or not key.startswith("env_file") or not code:
            continue
        if code not in codes:
            codes.append(code)
    return codes


def _git_command_status(project_root: Path, args: list[str]) -> int | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    return int(completed.returncode)


def _relative_to_root(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def _is_template_env_file(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".example") or name.endswith(".example.env")


def _env_file_git_safety_check(env_file: Path, root: Path) -> dict[str, Any]:
    relative_path = _relative_to_root(env_file, root)
    inside_project = relative_path is not None
    template_file = _is_template_env_file(env_file)
    git_available = _git_command_status(root, ["rev-parse", "--is-inside-work-tree"]) == 0
    git_tracked = False
    git_ignored = False
    issue_code = ""

    if inside_project and git_available:
        rel = relative_path.as_posix()
        git_tracked = _git_command_status(root, ["ls-files", "--error-unmatch", "--", rel]) == 0
        git_ignored = _git_command_status(root, ["check-ignore", "-q", "--", rel]) == 0
        if not template_file and git_tracked:
            issue_code = "env_file_tracked_by_git"
        elif not template_file and not git_ignored:
            issue_code = "env_file_not_gitignored"

    status = "fail" if issue_code else "pass"
    remediation = ""
    if issue_code == "env_file_tracked_by_git":
        remediation = (
            "Move real production env values to SOPS or a deployment secret store, "
            "remove the private env file from Git tracking, and rerun validation."
        )
    elif issue_code == "env_file_not_gitignored":
        remediation = (
            "Add the private production env path to .gitignore or place it outside the project, "
            "then rerun validation."
        )

    return {
        "key": "env_file_git_safety",
        "category": "secret_storage",
        "status": status,
        "set": inside_project,
        "placeholder_like": False,
        "default_like": False,
        "too_short": False,
        "expected": "private env files are untracked and ignored when stored inside the project",
        "matches_expected": status == "pass",
        "forbidden_value_shape": status == "fail",
        "required_for_production": True,
        "remediation": remediation,
        "inside_project": inside_project,
        "template_file": template_file,
        "git_available": git_available,
        "git_tracked": git_tracked,
        "git_ignored": git_ignored,
        "env_path": _safe_path_string(str(env_file), root),
        "config_keys": [],
        **({"issue_code": issue_code} if issue_code else {}),
    }


def _requires_secret_update(item: dict[str, Any]) -> bool:
    uri = item.get("uri")
    uri_contains_credentials = isinstance(uri, dict) and uri.get("contains_credentials") is True
    return item["category"] in {
        "database",
        "secrets",
        "object_storage",
        "observability",
        "secret_provenance",
        "optional_sensitive_config",
        "compose_deployment_secret",
    } or uri_contains_credentials


def _write_validation_report(
    report: dict[str, Any],
    *,
    destination: Path,
    stamp: str,
    project_root: Path,
) -> None:
    export_path = destination / f"production-env-validation-{stamp}.json"
    latest_path = destination / "production-env-validation-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")


def _env_file_failure_report(
    *,
    env_file: Path,
    root: Path,
    destination: Path,
    timestamp: datetime,
    stamp: str,
    issue_code: str,
    production_config_error: str,
    action: str,
    error_type: str | None = None,
) -> dict[str, Any]:
    next_actions = [
        {
            "key": "env_file",
            "label": "Production env file",
            "status": "fail",
            "category": "configuration",
            "action": action,
            "owner": "deployment_operator",
            "can_auto_fix": False,
            "requires_secret_update": True,
            "production_required": True,
            "config_keys": [],
            "verification_commands": [
                "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error",
                "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
            ],
            "verification": [
                {
                    "kind": "script",
                    "target": "scripts/validate_production_env.py",
                    "expectation": "status=pass",
                }
            ],
        }
    ]
    checks = [
        {
            "key": "env_file",
            "category": "configuration",
            "status": "fail",
            "set": False,
            "placeholder_like": False,
            "default_like": False,
            "too_short": False,
            "expected": None,
            "matches_expected": False,
            "forbidden_value_shape": False,
            "required_for_production": True,
            "remediation": action,
            "issue_code": issue_code,
        }
    ]
    if error_type:
        checks[0]["error_type"] = error_type
    env_file_issue_codes = _env_file_issue_codes(checks)
    report = {
        "report_type": "production_env_validation",
        "status": "fail",
        "release_allowed": False,
        "deployment_config_id": "",
        "generated_at": timestamp.isoformat(),
        "env_path": _safe_path_string(str(env_file), root),
        "summary": _summary(checks),
        "placeholder_count": 0,
        "default_like_count": 0,
        "missing_required_count": 1,
        "env_file_issue_count": len(env_file_issue_codes),
        "env_file_issue_codes": env_file_issue_codes,
        "next_action_count": len(next_actions),
        "next_actions": next_actions,
        "production_config_errors": [production_config_error],
        "checks": checks,
        "policy_basis": [
            "OWASP Secrets Management: production secrets must be managed through controlled secret stores and never echoed in reports.",
            "Twelve-Factor Config: deploy-specific config belongs in the environment, not code.",
            "Project production guard: APP_ENV=production must reject unsafe defaults before release.",
        ],
    }
    _write_validation_report(report, destination=destination, stamp=stamp, project_root=root)
    return report


def _missing_env_file_report(
    *,
    env_file: Path,
    root: Path,
    destination: Path,
    timestamp: datetime,
    stamp: str,
) -> dict[str, Any]:
    return _env_file_failure_report(
        env_file=env_file,
        root=root,
        destination=destination,
        timestamp=timestamp,
        stamp=stamp,
        issue_code="env_file_missing",
        production_config_error="Production env file was not found.",
        action=(
            "Materialize the approved apps/backend/.env.prod candidate from SOPS or the deployment "
            "secret store, validate it, then rerun production env validation."
        ),
    )


def validate_env_file(
    env_path: Path | str,
    *,
    project_root: Path | str | None = None,
    out_dir: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    destination = Path(out_dir) if out_dir is not None else root / "docs" / "verification"
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")

    env_file = Path(env_path)
    if not env_file.exists():
        return _missing_env_file_report(
            env_file=env_file,
            root=root,
            destination=destination,
            timestamp=timestamp,
            stamp=stamp,
        )

    try:
        values, parse_issues, key_line_numbers = parse_env_file_with_diagnostics(env_file)
    except UnicodeDecodeError as exc:
        return _env_file_failure_report(
            env_file=env_file,
            root=root,
            destination=destination,
            timestamp=timestamp,
            stamp=stamp,
            issue_code="env_file_read_error",
            production_config_error="Production env file could not be read as UTF-8 text.",
            action="Save the production env file as UTF-8 text and rerun scripts/validate_production_env.py.",
            error_type=type(exc).__name__,
        )
    except OSError as exc:
        return _env_file_failure_report(
            env_file=env_file,
            root=root,
            destination=destination,
            timestamp=timestamp,
            stamp=stamp,
            issue_code="env_file_read_error",
            production_config_error="Production env file could not be read.",
            action="Make the production env file readable and rerun scripts/validate_production_env.py.",
            error_type=type(exc).__name__,
        )
    checks = [_check_item(values, spec) for spec in PRODUCTION_CHECKS]
    checks.extend(_compose_deployment_value_checks(values))
    checks.extend(_optional_sensitive_value_checks(values))
    checks.extend(_source_metadata_checks(values))
    checks.append(_env_file_git_safety_check(env_file, root))
    syntax_issues = [item for item in parse_issues if item.get("issue") in {"missing_equals", "empty_key"}]
    duplicate_issues = [item for item in parse_issues if item.get("issue") == "duplicate_key"]
    known_keys = set(Settings.model_fields) | ALLOWED_METADATA_KEYS | set(COMPOSE_DEPLOYMENT_KEYS)
    unknown_key_lines = {key: key_line_numbers[key] for key in values if key not in known_keys}
    if syntax_issues:
        line_numbers = [int(item["line_number"]) for item in syntax_issues]
        checks.append(
            {
                "key": "env_file_syntax",
                "category": "configuration",
                "status": "fail",
                "set": False,
                "placeholder_like": False,
                "default_like": False,
                "too_short": False,
                "expected": None,
                "matches_expected": False,
                "forbidden_value_shape": False,
                "required_for_production": True,
                "remediation": "Fix malformed non-comment lines in the production env file and rerun scripts/validate_production_env.py.",
                "issue_code": "env_file_syntax_error",
                "line_numbers": line_numbers,
            }
        )
    if duplicate_issues:
        duplicate_keys = sorted({str(item["key"]) for item in duplicate_issues if item.get("key")})
        line_numbers = sorted(
            {
                int(item["first_line_number"])
                for item in duplicate_issues
                if item.get("first_line_number") is not None
            }
            | {int(item["line_number"]) for item in duplicate_issues}
        )
        checks.append(
            {
                "key": "env_file_duplicate_keys",
                "category": "configuration",
                "status": "fail",
                "set": False,
                "placeholder_like": False,
                "default_like": False,
                "too_short": False,
                "expected": None,
                "matches_expected": False,
                "forbidden_value_shape": False,
                "required_for_production": True,
                "remediation": "Remove duplicate keys in the production env file and rerun scripts/validate_production_env.py.",
                "issue_code": "env_file_duplicate_key",
                "duplicate_keys": duplicate_keys,
                "line_numbers": line_numbers,
            }
        )
    if unknown_key_lines:
        unknown_keys = sorted(unknown_key_lines)
        checks.append(
            {
                "key": "env_file_unknown_keys",
                "category": "configuration",
                "status": "fail",
                "set": False,
                "placeholder_like": False,
                "default_like": False,
                "too_short": False,
                "expected": None,
                "matches_expected": False,
                "forbidden_value_shape": False,
                "required_for_production": True,
                "remediation": "Remove or correct unknown keys in the production env file and rerun scripts/validate_production_env.py.",
                "issue_code": "env_file_unknown_key",
                "unknown_keys": unknown_keys,
                "line_numbers": [unknown_key_lines[key] for key in unknown_keys],
                "config_keys": unknown_keys,
            }
        )
    cfg = _settings_from_values(values)
    config_errors = production_config_errors(cfg)
    if syntax_issues:
        config_errors = [*config_errors, "Production env file contains malformed non-comment lines."]
    if duplicate_issues:
        config_errors = [*config_errors, "Production env file contains duplicate keys."]
    if unknown_key_lines:
        config_errors = [*config_errors, "Production env file contains unknown keys."]
    placeholder_count = sum(1 for item in checks if item["placeholder_like"])
    default_like_count = sum(1 for item in checks if item["default_like"])
    missing_required_count = sum(1 for item in checks if item["required_for_production"] and not item["set"])
    env_file_issue_codes = _env_file_issue_codes(checks)
    status = "fail" if any(item["status"] == "fail" for item in checks) or config_errors else "pass"
    next_actions = [
        {
            "key": item["key"],
            "label": item["key"],
            "status": item["status"],
            "category": item["category"],
            "action": item["remediation"],
            "owner": "deployment_operator",
            "can_auto_fix": False,
            "requires_secret_update": _requires_secret_update(item),
            "production_required": True,
            "config_keys": item.get("config_keys", [item["key"]]),
            **(
                {"alternative_config_keys": item["alternative_config_keys"]}
                if isinstance(item.get("alternative_config_keys"), list)
                else {}
            ),
            **(
                {
                    "source_provenance_target_config_keys": item[
                        "source_provenance_target_config_keys"
                    ]
                }
                if isinstance(item.get("source_provenance_target_config_keys"), list)
                else {}
            ),
            **({"uri": item["uri"]} if isinstance(item.get("uri"), dict) else {}),
            "verification": [
                {
                    "kind": "script",
                    "target": "scripts/validate_production_env.py",
                    "expectation": f"checks.{item['key']}.status=pass",
                }
            ],
        }
        for item in checks
        if item["status"] == "fail"
    ]

    report = {
        "report_type": "production_env_validation",
        "status": status,
        "release_allowed": status == "pass",
        "deployment_config_id": values.get("DEPLOYMENT_CONFIG_ID", "").strip(),
        "generated_at": timestamp.isoformat(),
        "env_path": _safe_path_string(str(env_file), root),
        "summary": _summary(checks),
        "placeholder_count": placeholder_count,
        "default_like_count": default_like_count,
        "missing_required_count": missing_required_count,
        "env_file_issue_count": len(env_file_issue_codes),
        "env_file_issue_codes": env_file_issue_codes,
        "next_action_count": len(next_actions),
        "next_actions": next_actions,
        "production_config_errors": config_errors,
        "checks": checks,
        "policy_basis": [
            "OWASP Secrets Management: production secrets must be managed through controlled secret stores and never echoed in reports.",
            "Twelve-Factor Config: deploy-specific config belongs in the environment, not code.",
            "Project production guard: APP_ENV=production must reject unsafe defaults before release.",
        ],
    }
    _write_validation_report(report, destination=destination, stamp=stamp, project_root=root)
    return report


def public_cli_result(result: dict[str, Any], *, project_root: Path | str | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()

    def sanitize(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, str):
            return _safe_path_string(value, root)
        return value

    return sanitize(result)


def format_cli_json(result: dict[str, Any]) -> str:
    return json.dumps(public_cli_result(result), ensure_ascii=True, indent=2)


def validation_exit_code(result: dict[str, Any], *, fail_on_error: bool) -> int:
    if not fail_on_error:
        return 0
    return 0 if result.get("status") == "pass" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a production env file without printing secret values.")
    parser.add_argument("--env-file", default="apps/backend/.env.prod.example")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--fail-on-error", action="store_true", help="Exit 2 when the env validation status is not pass.")
    args = parser.parse_args()

    result = validate_env_file(
        Path(args.env_file),
        out_dir=Path(args.out_dir) if args.out_dir else None,
    )
    print(format_cli_json(result))
    return validation_exit_code(result, fail_on_error=args.fail_on_error)


if __name__ == "__main__":
    raise SystemExit(main())
