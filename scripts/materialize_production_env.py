from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "docs" / "verification"
DEFAULT_OUTPUT = PROJECT_ROOT / "apps" / "backend" / ".env.prod"


OUTPUT_KEY_ORDER = [
    "APP_ENV",
    "DEPLOYMENT_CONFIG_ID",
    "APP_DEBUG",
    "OPENROUTER_MOCK",
    "SECRETS_SOURCE",
    "DATABASE_URL",
    "PGVECTOR_ENABLED",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "APP_API_KEY",
    "APP_SECRET_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "REDIS_ENABLED",
    "REDIS_URL",
    "RATE_LIMIT_STORAGE_URI",
    "OBJECT_STORAGE_PROVIDER",
    "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION",
    "MINIO_ENDPOINT",
    "MINIO_SECURE",
    "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION",
    "MINIO_BUCKET",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "LOG_FORMAT",
    "LOG_LEVEL",
    "LOG_DIR",
    "LOG_RETENTION_DAYS",
    "SENTRY_DSN",
    "SENTRY_ENVIRONMENT",
    "SENTRY_TRACES_SAMPLE_RATE",
    "APP_CORS_ORIGINS",
]

REQUIRED_KEYS = [
    "DEPLOYMENT_CONFIG_ID",
    "APP_API_KEY",
    "APP_SECRET_KEY",
    "OPENROUTER_API_KEY",
    "POSTGRES_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "SENTRY_DSN",
    "SECRETS_SOURCE",
    "DATABASE_URL",
]

ACCEPTED_SOURCE_PREFIXES = [
    "sops:",
    "secret-store:",
    "vault:",
    "aws-secrets-manager:",
    "gcp-secret-manager:",
    "azure-key-vault:",
    "kubernetes-secret:",
    "doppler:",
    "1password:",
]

MISSING_CONFIG_REQUEST_SPECS: dict[str, dict[str, Any]] = {
    "DEPLOYMENT_CONFIG_ID": {
        "sensitivity": "non_secret_config",
        "suggested_sops_key": "adms_deployment_config_id",
        "source_marker_key": "DEPLOYMENT_CONFIG_ID_SOURCE",
        "description": "Stable release identity for the approved production configuration.",
    },
    "APP_API_KEY": {
        "sensitivity": "secret",
        "suggested_sops_key": "adms_app_api_key",
        "source_marker_key": "APP_API_KEY_SOURCE",
        "description": "Bearer/API key used to authenticate privileged backend requests.",
    },
    "APP_SECRET_KEY": {
        "sensitivity": "secret",
        "suggested_sops_key": "adms_app_secret_key",
        "source_marker_key": "APP_SECRET_KEY_SOURCE",
        "description": "Application signing/encryption secret; must be at least 32 characters.",
    },
    "OPENROUTER_API_KEY": {
        "sensitivity": "secret",
        "suggested_sops_key": "openrouter_key",
        "source_marker_key": "OPENROUTER_API_KEY_SOURCE",
        "description": "OpenRouter model gateway API key.",
    },
    "POSTGRES_PASSWORD": {
        "sensitivity": "secret",
        "suggested_sops_key": "adms_postgres_password",
        "source_marker_key": "POSTGRES_PASSWORD_SOURCE",
        "description": "PostgreSQL application user password.",
    },
    "MINIO_ROOT_USER": {
        "sensitivity": "credential_identifier",
        "suggested_sops_key": "adms_minio_root_user",
        "source_marker_key": "MINIO_ROOT_USER_SOURCE",
        "description": "Production MinIO root/access user for object storage bootstrap.",
    },
    "MINIO_ROOT_PASSWORD": {
        "sensitivity": "secret",
        "suggested_sops_key": "adms_minio_root_password",
        "source_marker_key": "MINIO_ROOT_PASSWORD_SOURCE",
        "description": "Production MinIO root/access password.",
    },
    "SENTRY_DSN": {
        "sensitivity": "operational_secret",
        "suggested_sops_key": "adms_sentry_dsn",
        "source_marker_key": "SENTRY_DSN_SOURCE",
        "description": "Sentry DSN for production exception telemetry.",
    },
    "DATABASE_URL": {
        "sensitivity": "secret_uri",
        "suggested_sops_key": "adms_database_url",
        "source_marker_key": "DATABASE_URL_SOURCE",
        "can_be_derived_from": ["POSTGRES_PASSWORD"],
        "description": "PostgreSQL connection URI; can be derived when POSTGRES_PASSWORD is provided.",
    },
}

CONFIG_VALUE_ALIASES: dict[str, list[tuple[str, str]]] = {
    "DEPLOYMENT_CONFIG_ID": [
        ("DEPLOYMENT_CONFIG_ID", "standard_env"),
        ("adms_deployment_config_id", "project_sops_short_name"),
        ("ADMS_DEPLOYMENT_CONFIG_ID", "project_env_alias"),
    ],
    "APP_API_KEY": [
        ("APP_API_KEY", "standard_env"),
        ("adms_app_api_key", "project_sops_short_name"),
        ("ADMS_APP_API_KEY", "project_env_alias"),
    ],
    "APP_SECRET_KEY": [
        ("APP_SECRET_KEY", "standard_env"),
        ("adms_app_secret_key", "project_sops_short_name"),
        ("ADMS_APP_SECRET_KEY", "project_env_alias"),
    ],
    "POSTGRES_PASSWORD": [
        ("POSTGRES_PASSWORD", "standard_env"),
        ("adms_postgres_password", "project_sops_short_name"),
        ("ADMS_POSTGRES_PASSWORD", "project_env_alias"),
    ],
    "MINIO_ROOT_USER": [
        ("MINIO_ROOT_USER", "standard_env"),
        ("adms_minio_root_user", "project_sops_short_name"),
        ("ADMS_MINIO_ROOT_USER", "project_env_alias"),
        ("MINIO_ACCESS_KEY", "standard_env_fallback"),
    ],
    "MINIO_ROOT_PASSWORD": [
        ("MINIO_ROOT_PASSWORD", "standard_env"),
        ("adms_minio_root_password", "project_sops_short_name"),
        ("ADMS_MINIO_ROOT_PASSWORD", "project_env_alias"),
        ("MINIO_SECRET_KEY", "standard_env_fallback"),
    ],
    "SENTRY_DSN": [
        ("SENTRY_DSN", "standard_env"),
        ("adms_sentry_dsn", "project_sops_short_name"),
        ("ADMS_SENTRY_DSN", "project_env_alias"),
    ],
    "DATABASE_URL": [
        ("DATABASE_URL", "standard_env"),
        ("adms_database_url", "project_sops_short_name"),
        ("ADMS_DATABASE_URL", "project_env_alias"),
    ],
}


def project_root_from_script() -> Path:
    return PROJECT_ROOT


def _ensure_inside_project(path: Path, project_root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = project_root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Path must be inside project: {resolved_root}") from exc
    return resolved_path


def _safe_path_string(value: str | Path, project_root: Path) -> str:
    normalized = str(value).replace("\\", "/")
    root = str(project_root.resolve()).replace("\\", "/")
    if normalized.startswith(root + "/"):
        return normalized[len(root) + 1 :]
    marker = "docs/verification/"
    if marker in normalized:
        return marker + normalized.split(marker, 1)[1]
    if ":/" in normalized:
        return Path(normalized).name
    return normalized


def _sanitize_export(value: Any, project_root: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_export(item, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_export(item, project_root) for item in value]
    if isinstance(value, str):
        return _safe_path_string(value, project_root)
    return value


def public_cli_result(report: dict[str, Any], *, project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or project_root_from_script()
    return _sanitize_export(report, root)


def format_cli_json(report: dict[str, Any]) -> str:
    return json.dumps(public_cli_result(report), ensure_ascii=True, indent=2)


def _write_report(
    report: dict[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    project_root: Path,
) -> dict[str, Any]:
    export_path = out_dir / f"production-env-materialization-{stamp}.json"
    latest_path = out_dir / "production-env-materialization-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return json.loads(text)


def _clean_mapping(values: Mapping[str, str] | None) -> dict[str, str]:
    if values is None:
        values = os.environ
    result: dict[str, str] = {}
    for key, value in values.items():
        text = str(value)
        if text:
            result[str(key)] = text
    return result


def _value(values: Mapping[str, str], key: str, default: str = "") -> str:
    return str(values.get(key, "") or default).strip()


def _first_value(values: Mapping[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        value = _value(values, key)
        if value:
            return value
    return default


def _config_value(values: Mapping[str, str], key: str, default: str = "") -> str:
    for alias, _label in CONFIG_VALUE_ALIASES.get(key, [(key, "standard_env")]):
        value = _value(values, alias)
        if value:
            return value
    return default


def _config_alias_resolution(values: Mapping[str, str], key: str) -> str:
    for alias, label in CONFIG_VALUE_ALIASES.get(key, [(key, "standard_env")]):
        if _value(values, alias):
            return label
    return ""


def _openrouter_alias_resolution(values: Mapping[str, str]) -> str:
    if _value(values, "OPENROUTER_API_KEY"):
        return "standard_env"
    if _value(values, "OPENROUTER_KEY"):
        return "legacy_env_alias"
    if _value(values, "openrouter_key"):
        return "sops_short_name"
    return ""


def _derive_database_url(values: Mapping[str, str], output: dict[str, str]) -> str:
    explicit = _config_value(values, "DATABASE_URL")
    if explicit:
        return explicit
    password = _value(output, "POSTGRES_PASSWORD")
    if not password:
        return ""
    user = _value(output, "POSTGRES_USER", "agent_user")
    db = _value(output, "POSTGRES_DB", "agent_db")
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@postgres:5432/{quote(db, safe='')}"


def _normalized_env(values: Mapping[str, str], *, source_label: str = "") -> dict[str, str]:
    output: dict[str, str] = {
        "APP_ENV": _value(values, "APP_ENV", "production"),
        "DEPLOYMENT_CONFIG_ID": _value(values, "DEPLOYMENT_CONFIG_ID"),
        "APP_DEBUG": _value(values, "APP_DEBUG", "false"),
        "OPENROUTER_MOCK": _value(values, "OPENROUTER_MOCK", "false"),
        "SECRETS_SOURCE": _value(values, "SECRETS_SOURCE", source_label),
        "PGVECTOR_ENABLED": _value(values, "PGVECTOR_ENABLED", "true"),
        "POSTGRES_DB": _value(values, "POSTGRES_DB", "agent_db"),
        "POSTGRES_USER": _value(values, "POSTGRES_USER", "agent_user"),
        "POSTGRES_PASSWORD": _config_value(values, "POSTGRES_PASSWORD"),
        "APP_API_KEY": _config_value(values, "APP_API_KEY"),
        "APP_SECRET_KEY": _config_value(values, "APP_SECRET_KEY"),
        "OPENROUTER_API_KEY": _first_value(
            values,
            "OPENROUTER_API_KEY",
            "OPENROUTER_KEY",
            "openrouter_key",
        ),
        "OPENROUTER_BASE_URL": _value(values, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "REDIS_ENABLED": _value(values, "REDIS_ENABLED", "true"),
        "REDIS_URL": _value(values, "REDIS_URL", "redis://redis:6379/0"),
        "RATE_LIMIT_STORAGE_URI": _value(values, "RATE_LIMIT_STORAGE_URI", "redis://redis:6379/1"),
        "OBJECT_STORAGE_PROVIDER": _value(values, "OBJECT_STORAGE_PROVIDER", "minio"),
        "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION": _value(values, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", "true"),
        "MINIO_ENDPOINT": _value(values, "MINIO_ENDPOINT", "minio:9000"),
        "MINIO_SECURE": _value(values, "MINIO_SECURE", "true"),
        "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION": _value(
            values,
            "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION",
            "false",
        ),
        "MINIO_BUCKET": _value(values, "MINIO_BUCKET", "agent-files-prod"),
        "MINIO_ROOT_USER": _config_value(values, "MINIO_ROOT_USER"),
        "MINIO_ROOT_PASSWORD": _config_value(values, "MINIO_ROOT_PASSWORD"),
        "LOG_FORMAT": _value(values, "LOG_FORMAT", "json"),
        "LOG_LEVEL": _value(values, "LOG_LEVEL", "INFO"),
        "LOG_DIR": _value(values, "LOG_DIR", "/var/log/researchos"),
        "LOG_RETENTION_DAYS": _value(values, "LOG_RETENTION_DAYS", "30"),
        "SENTRY_DSN": _config_value(values, "SENTRY_DSN"),
        "SENTRY_ENVIRONMENT": _value(values, "SENTRY_ENVIRONMENT", "production"),
        "SENTRY_TRACES_SAMPLE_RATE": _value(values, "SENTRY_TRACES_SAMPLE_RATE", "0.05"),
        "APP_CORS_ORIGINS": _value(values, "APP_CORS_ORIGINS"),
    }
    output["DEPLOYMENT_CONFIG_ID"] = _config_value(values, "DEPLOYMENT_CONFIG_ID")
    output["DATABASE_URL"] = _derive_database_url(values, output)
    output["MINIO_ACCESS_KEY"] = _value(values, "MINIO_ACCESS_KEY") or output["MINIO_ROOT_USER"]
    output["MINIO_SECRET_KEY"] = _value(values, "MINIO_SECRET_KEY") or output["MINIO_ROOT_PASSWORD"]
    return output


def _env_text(values: Mapping[str, str]) -> str:
    lines = [
        "# Generated by scripts/materialize_production_env.py",
        "# This file is ignored by git. Do not commit filled production values.",
    ]
    for key in OUTPUT_KEY_ORDER:
        value = str(values.get(key, "") or "")
        if "\n" in value or "\r" in value:
            raise ValueError(f"Environment value contains a newline: {key}")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _missing_required_keys(values: Mapping[str, str]) -> list[str]:
    return [key for key in REQUIRED_KEYS if not str(values.get(key, "") or "").strip()]


def _missing_config_requests(missing: list[str]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for key in missing:
        spec = MISSING_CONFIG_REQUEST_SPECS.get(key, {})
        requests.append(
            {
                "config_key": key,
                "suggested_sops_key": str(spec.get("suggested_sops_key") or f"adms_{key.lower()}"),
                "source_marker_key": str(spec.get("source_marker_key") or f"{key}_SOURCE"),
                "sensitivity": str(spec.get("sensitivity") or "secret"),
                "can_be_derived_from": list(spec.get("can_be_derived_from") or []),
                "accepted_source_prefixes": ACCEPTED_SOURCE_PREFIXES,
                "description": str(spec.get("description") or ""),
            }
        )
    return requests


def _next_actions(*, status: str, missing: list[str], output_exists: bool) -> list[dict[str, Any]]:
    if status == "pass":
        return []
    if output_exists:
        return [
            {
                "key": "output_exists",
                "label": "Production env output already exists",
                "status": "blocked",
                "action": "Review the existing production env candidate and rerun with --overwrite only if replacement is intended.",
                "owner": "deployment_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
            }
        ]
    return [
        {
            "key": "missing_required_config",
            "label": "Missing required production config",
            "status": "fail",
            "action": "Inject the missing keys through SOPS or the deployment secret store, then rerun scripts/materialize_production_env.py.",
            "owner": "deployment_operator",
            "can_auto_fix": False,
            "requires_secret_update": True,
            "production_required": True,
            "config_keys": missing,
        }
    ]


def materialize_production_env(
    *,
    env_values: Mapping[str, str] | None = None,
    output: Path | str | None = None,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    overwrite: bool = False,
    source_label: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    output_path = Path(output) if output is not None else DEFAULT_OUTPUT
    if not output_path.is_absolute():
        output_path = root / output_path
    output_path = _ensure_inside_project(output_path, root)

    destination = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    if not destination.is_absolute():
        destination = root / destination
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    source_values = _clean_mapping(env_values)
    output_values = _normalized_env(source_values, source_label=source_label)
    missing = _missing_required_keys(output_values)
    missing_requests = _missing_config_requests(missing)
    exists = output_path.exists()
    blocked_by_existing_output = exists and not overwrite and not dry_run
    status = "pass"
    if missing:
        status = "fail"
    elif blocked_by_existing_output:
        status = "blocked"

    output_written = False
    chmod_applied = False
    if status == "pass" and not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_env_text(output_values), encoding="utf-8", newline="\n")
        output_written = True
        try:
            os.chmod(output_path, 0o600)
            chmod_applied = True
        except OSError:
            chmod_applied = False

    report = {
        "report_type": "production_env_materialization",
        "status": status,
        "generated_at": timestamp.isoformat(),
        "source": "provided_mapping" if env_values is not None else "process_environment",
        "output_path": str(output_path),
        "output_written": output_written,
        "would_write_output": status == "pass",
        "output_exists": exists,
        "overwrite": overwrite,
        "dry_run": dry_run,
        "chmod_0600_applied": chmod_applied,
        "missing_required_key_count": len(missing),
        "missing_required_keys": missing,
        "missing_config_request_count": len(missing_requests),
        "missing_config_requests": missing_requests,
        "provided_key_count": len(source_values),
        "written_key_count": len(OUTPUT_KEY_ORDER) if output_written else 0,
        "written_keys": OUTPUT_KEY_ORDER if output_written else [],
        "alias_resolution": {
            "DEPLOYMENT_CONFIG_ID": _config_alias_resolution(source_values, "DEPLOYMENT_CONFIG_ID"),
            "APP_API_KEY": _config_alias_resolution(source_values, "APP_API_KEY"),
            "APP_SECRET_KEY": _config_alias_resolution(source_values, "APP_SECRET_KEY"),
            "OPENROUTER_API_KEY": _openrouter_alias_resolution(source_values),
            "POSTGRES_PASSWORD": _config_alias_resolution(source_values, "POSTGRES_PASSWORD"),
            "DATABASE_URL": _config_alias_resolution(source_values, "DATABASE_URL")
            or (
                "derived_from_project_sops_short_name"
                if _config_alias_resolution(source_values, "POSTGRES_PASSWORD") == "project_sops_short_name"
                else "derived_from_project_env_alias"
                if _config_alias_resolution(source_values, "POSTGRES_PASSWORD") == "project_env_alias"
                else "derived_from_standard_env"
                if _config_alias_resolution(source_values, "POSTGRES_PASSWORD")
                else ""
            ),
            "MINIO_ROOT_USER": _config_alias_resolution(source_values, "MINIO_ROOT_USER"),
            "MINIO_ROOT_PASSWORD": _config_alias_resolution(source_values, "MINIO_ROOT_PASSWORD"),
            "MINIO_ACCESS_KEY": "MINIO_ACCESS_KEY" if _value(source_values, "MINIO_ACCESS_KEY") else "MINIO_ROOT_USER",
            "MINIO_SECRET_KEY": "MINIO_SECRET_KEY" if _value(source_values, "MINIO_SECRET_KEY") else "MINIO_ROOT_PASSWORD",
            "SENTRY_DSN": _config_alias_resolution(source_values, "SENTRY_DSN"),
        },
        "next_action_count": len(_next_actions(status=status, missing=missing, output_exists=blocked_by_existing_output)),
        "next_actions": _next_actions(status=status, missing=missing, output_exists=blocked_by_existing_output),
        "verification_commands": [
            "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
            "python scripts/check_production_compose_config.py --env-file apps/backend/.env.prod --fail-on-error",
            "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked",
        ],
        "policy_basis": [
            "The script never prints or stores secret values in its JSON report.",
            "The materialized env file is intended for a git-ignored local or deployment candidate only.",
            "Missing required values block file creation to avoid partial production candidates.",
        ],
    }
    return _write_report(report, out_dir=destination, stamp=stamp, project_root=root)


def exit_code(report: dict[str, Any], *, fail_on_error: bool) -> int:
    if fail_on_error and report.get("status") != "pass":
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize a git-ignored production env candidate from the current SOPS/deployment environment."
    )
    parser.add_argument("--output", default="apps/backend/.env.prod")
    parser.add_argument("--out-dir", default="docs/verification")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--source-label",
        default="",
        help="Trusted source metadata to use when SECRETS_SOURCE is not already set, for example sops:production.",
    )
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    report = materialize_production_env(
        output=Path(args.output),
        out_dir=Path(args.out_dir),
        overwrite=args.overwrite,
        source_label=args.source_label,
        dry_run=args.dry_run,
    )
    print(format_cli_json(report))
    return exit_code(report, fail_on_error=args.fail_on_error)


if __name__ == "__main__":
    raise SystemExit(main())
