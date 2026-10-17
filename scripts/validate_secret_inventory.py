from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_production_env import (  # noqa: E402
    OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS,
    PLACEHOLDER_PATTERNS,
    PRODUCTION_CHECKS,
    SOURCE_PROVENANCE_CONFIG_KEYS,
    parse_env_file,
)
from app.utils.secret_source_policy import (  # noqa: E402
    ACCEPTED_SECRET_SOURCE_PREFIXES,
    source_family as _source_family,
    trusted_secret_source as _trusted_source_provenance,
)


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


def _placeholder_like(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in PLACEHOLDER_PATTERNS)


def _uri_metadata(value: str) -> dict[str, Any]:
    parsed = urlparse((value or "").strip())
    return {
        "scheme": parsed.scheme,
        "host_set": bool(parsed.hostname),
        "port_set": parsed.port is not None,
        "contains_credentials": bool(parsed.username or parsed.password),
    }


def _source_provenance(key: str, env_values: Mapping[str, str]) -> dict[str, Any]:
    key_marker = f"{key}_SOURCE"
    for marker_key in (key_marker, "SECRETS_SOURCE"):
        marker_value = str(env_values.get(marker_key, "") or "").strip()
        if not marker_value:
            continue
        trusted = _trusted_source_provenance(marker_value)
        return {
            "source_marker_present": True,
            "source_marker_key": marker_key,
            "source_marker_family": _source_family(marker_value) if trusted else "untrusted",
            "source_provenance_ok": trusted,
            "source_provenance_required": True,
        }
    return {
        "source_marker_present": False,
        "source_marker_key": "",
        "source_marker_family": "",
        "source_provenance_ok": False,
        "source_provenance_required": True,
    }


def _production_spec_by_key() -> dict[str, dict[str, Any]]:
    return {str(item["key"]): item for item in PRODUCTION_CHECKS}


def _inventory_items(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = inventory.get("items")
    if isinstance(raw_items, list) and raw_items:
        return [item for item in raw_items if isinstance(item, dict) and str(item.get("key") or "").strip()]
    keys = inventory.get("secret_config_keys")
    if not isinstance(keys, list):
        return []
    items: list[dict[str, Any]] = []
    for key in keys:
        text = str(key or "").strip()
        if text:
            items.append({"key": text, "classification": "secret"})
    return items


def _inventory_source_provenance_target_config_keys(inventory: dict[str, Any]) -> list[str]:
    values = inventory.get("source_provenance_target_config_keys")
    if not isinstance(values, list):
        return []
    allowed = set(SOURCE_PROVENANCE_CONFIG_KEYS)
    keys: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text in allowed and text not in keys:
            keys.append(text)
    return keys


def _invalid_source_provenance_target_config_keys(inventory: dict[str, Any]) -> list[str]:
    values = inventory.get("source_provenance_target_config_keys")
    if values is None:
        return []
    if not isinstance(values, list):
        return ["<non-list>"]
    allowed = set(SOURCE_PROVENANCE_CONFIG_KEYS)
    invalid: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in allowed:
            continue
        if text not in invalid:
            invalid.append(text)
    return invalid


def _source_provenance_target_config_key_issue_check(
    invalid_keys: list[str],
) -> dict[str, Any] | None:
    if not invalid_keys:
        return None
    return {
        "key": "inventory_source_provenance_target_config_keys",
        "status": "fail",
        "classification": "inventory_schema",
        "set": True,
        "placeholder_like": False,
        "default_like": False,
        "too_short": False,
        "forbidden_value_shape": True,
        "missing_uri_credentials": False,
        "source_marker_present": False,
        "source_marker_key": "",
        "source_marker_family": "",
        "source_provenance_ok": False,
        "source_provenance_required": False,
        "config_keys": [],
        "alternative_config_keys": [],
        "invalid_source_provenance_target_config_keys": invalid_keys,
        "issue_code": "invalid_source_provenance_target_config_keys",
        "recommended_source": "release gate generated inventory",
        "source_steps": [],
        "action_keys": ["inventory_source_provenance_target_config_keys"],
        "categories": ["inventory_schema"],
        "owners": ["release_operator"],
        "remediation": (
            "Regenerate the release gate secret inventory so source_provenance_target_config_keys "
            "contains production config keys such as REDIS_URL, not *_SOURCE metadata keys or unknown keys."
        ),
    }


def _int_field(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _string_list_field(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _inventory_self_consistency_check(
    inventory: dict[str, Any],
    *,
    inventory_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    issue_codes: list[str] = []
    details: dict[str, Any] = {}
    item_keys = [str(item.get("key") or "").strip() for item in inventory_items if str(item.get("key") or "").strip()]
    secret_config_keys = _string_list_field(inventory.get("secret_config_keys"))
    if "secret_config_key_count" in inventory:
        declared_count = _int_field(inventory.get("secret_config_key_count"))
        if declared_count is None or declared_count != len(secret_config_keys or item_keys):
            issue_codes.append("secret_config_key_count_mismatch")
            details["declared_secret_config_key_count"] = inventory.get("secret_config_key_count")
            details["actual_secret_config_key_count"] = len(secret_config_keys or item_keys)
    if secret_config_keys is not None and secret_config_keys != item_keys:
        issue_codes.append("secret_config_keys_items_mismatch")
        details["declared_secret_config_keys"] = secret_config_keys
        details["item_secret_config_keys"] = item_keys

    source_provenance_key = str(inventory.get("source_provenance_key") or "").strip()
    if source_provenance_key and source_provenance_key != "SECRETS_SOURCE":
        issue_codes.append("source_provenance_key_invalid")
        details["source_provenance_key"] = source_provenance_key

    source_provenance_keys = _string_list_field(inventory.get("source_provenance_keys"))
    if source_provenance_keys is not None:
        if "source_provenance_key_count" in inventory:
            declared_count = _int_field(inventory.get("source_provenance_key_count"))
            if declared_count is None or declared_count != len(source_provenance_keys):
                issue_codes.append("source_provenance_key_count_mismatch")
                details["declared_source_provenance_key_count"] = inventory.get("source_provenance_key_count")
                details["actual_source_provenance_key_count"] = len(source_provenance_keys)
        if source_provenance_keys[:1] != ["SECRETS_SOURCE"]:
            issue_codes.append("source_provenance_keys_missing_global")
        expected_secret_source_keys = [f"{key}_SOURCE" for key in item_keys]
        target_source_keys = [
            f"{key}_SOURCE" for key in _inventory_source_provenance_target_config_keys(inventory)
        ]
        allowed_source_provenance_keys = {
            "SECRETS_SOURCE",
            *expected_secret_source_keys,
            *target_source_keys,
        }
        invalid_source_provenance_keys = [
            key for key in source_provenance_keys if key not in allowed_source_provenance_keys
        ]
        if invalid_source_provenance_keys:
            issue_codes.append("invalid_source_provenance_keys")
            details["invalid_source_provenance_keys"] = invalid_source_provenance_keys
        missing_secret_source_keys = [
            key for key in expected_secret_source_keys if key not in source_provenance_keys
        ]
        if missing_secret_source_keys:
            issue_codes.append("missing_secret_source_provenance_keys")
            details["missing_secret_source_provenance_keys"] = missing_secret_source_keys
        missing_target_source_keys = [
            key for key in target_source_keys if key not in source_provenance_keys
        ]
        if missing_target_source_keys:
            issue_codes.append("missing_target_source_provenance_keys")
            details["missing_target_source_provenance_keys"] = missing_target_source_keys

    per_key_source_provenance_keys = _string_list_field(
        inventory.get("per_key_source_provenance_keys")
    )
    if per_key_source_provenance_keys is not None:
        if "per_key_source_provenance_key_count" in inventory:
            declared_count = _int_field(inventory.get("per_key_source_provenance_key_count"))
            if declared_count is None or declared_count != len(per_key_source_provenance_keys):
                issue_codes.append("per_key_source_provenance_key_count_mismatch")
                details["declared_per_key_source_provenance_key_count"] = inventory.get(
                    "per_key_source_provenance_key_count"
                )
                details["actual_per_key_source_provenance_key_count"] = len(
                    per_key_source_provenance_keys
                )
        if source_provenance_keys is not None:
            expected_per_key_source_keys = [
                key for key in source_provenance_keys if key != "SECRETS_SOURCE"
            ]
            if per_key_source_provenance_keys != expected_per_key_source_keys:
                issue_codes.append("per_key_source_provenance_keys_mismatch")
                details["expected_per_key_source_provenance_keys"] = expected_per_key_source_keys
                details["per_key_source_provenance_keys"] = per_key_source_provenance_keys

    if not issue_codes:
        return None
    return {
        "key": "inventory_self_consistency",
        "status": "fail",
        "classification": "inventory_schema",
        "set": True,
        "placeholder_like": False,
        "default_like": False,
        "too_short": False,
        "forbidden_value_shape": True,
        "missing_uri_credentials": False,
        "source_marker_present": False,
        "source_marker_key": "",
        "source_marker_family": "",
        "source_provenance_ok": False,
        "source_provenance_required": False,
        "config_keys": [],
        "alternative_config_keys": [],
        "issue_codes": issue_codes,
        **details,
        "recommended_source": "release gate generated inventory",
        "source_steps": [],
        "action_keys": ["inventory_self_consistency"],
        "categories": ["inventory_schema"],
        "owners": ["release_operator"],
        "remediation": (
            "Regenerate the release gate secret inventory so declared counts, secret key lists, "
            "source provenance keys, and per-key source provenance keys are internally consistent."
        ),
    }


def _check_inventory_item(
    item: dict[str, Any],
    *,
    env_values: Mapping[str, str],
    specs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = str(item.get("key") or "").strip()
    spec = specs.get(key, {})
    raw = str(env_values.get(key, "") or "")
    text = raw.strip()
    classification = str(item.get("classification") or "secret")
    set_ok = bool(text)
    placeholder_like = _placeholder_like(text)
    default_like = text in set(spec.get("default_values", []))
    min_length = int(spec.get("min_length", 1))
    too_short = set_ok and len(text) < min_length
    forbidden_prefixes = [str(prefix).lower() for prefix in spec.get("forbidden_prefixes", [])]
    forbidden = any(text.lower().startswith(prefix) for prefix in forbidden_prefixes)
    needs_uri = classification == "credential_bearing_uri" or bool(spec.get("uri_metadata"))
    uri = _uri_metadata(text) if needs_uri else None
    missing_uri_credentials = classification == "credential_bearing_uri" and not bool(
        uri and uri.get("contains_credentials")
    )
    provenance = _source_provenance(key, env_values)
    source_provenance_ok = bool(provenance["source_provenance_ok"])
    failed = bool(
        not set_ok
        or placeholder_like
        or default_like
        or too_short
        or forbidden
        or missing_uri_credentials
        or not source_provenance_ok
    )
    if not source_provenance_ok:
        remediation = (
            f"Inject {key} through SOPS or another controlled secret source, set "
            f"{key}_SOURCE or SECRETS_SOURCE provenance metadata, and rerun validation."
        )
    else:
        remediation = spec.get("remediation") or (
            f"Inject {key} through SOPS or the deployment secret store and rerun validation."
        )
    check: dict[str, Any] = {
        "key": key,
        "status": "fail" if failed else "pass",
        "classification": classification,
        "set": set_ok,
        "placeholder_like": placeholder_like,
        "default_like": default_like,
        "too_short": too_short,
        "forbidden_value_shape": forbidden,
        "missing_uri_credentials": missing_uri_credentials,
        **provenance,
        "recommended_source": item.get("recommended_source") or "SOPS or deployment secret store",
        "source_steps": item.get("source_steps") if isinstance(item.get("source_steps"), list) else [],
        "action_keys": item.get("action_keys") if isinstance(item.get("action_keys"), list) else [],
        "categories": item.get("categories") if isinstance(item.get("categories"), list) else [],
        "owners": item.get("owners") if isinstance(item.get("owners"), list) else ["deployment_operator"],
        "remediation": remediation if failed else "",
    }
    if uri is not None:
        check["uri"] = uri
    return check


def _check_source_provenance_target(
    target_config_key: str,
    *,
    env_values: Mapping[str, str],
) -> dict[str, Any]:
    marker_key = f"{target_config_key}_SOURCE"
    provenance = _source_provenance(target_config_key, env_values)
    source_provenance_ok = bool(provenance["source_provenance_ok"])
    target_value = str(env_values.get(target_config_key, "") or "").strip()
    stale_optional_source_metadata = (
        target_config_key in OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS
        and not target_value
    )
    invalid_optional_sensitive_value = (
        target_config_key in OPTIONAL_SOURCE_PROVENANCE_CONFIG_KEYS
        and bool(target_value)
        and _placeholder_like(target_value)
    )
    status = (
        "fail"
        if stale_optional_source_metadata
        or invalid_optional_sensitive_value
        or not source_provenance_ok
        else "pass"
    )
    config_keys = [marker_key]
    alternative_config_keys = ["SECRETS_SOURCE"]
    issue_code: str | None = None
    if stale_optional_source_metadata:
        alternative_config_keys = [target_config_key]
        issue_code = "stale_optional_source_metadata"
        remediation = (
            f"Remove stale {marker_key}, or set {target_config_key} if that optional integration is intentionally enabled."
        )
    elif invalid_optional_sensitive_value:
        config_keys = [target_config_key]
        alternative_config_keys = [marker_key]
        issue_code = "invalid_optional_sensitive_value"
        remediation = (
            f"Set {target_config_key} to a real value from SOPS or the deployment secret store, "
            f"or remove {target_config_key} and {marker_key} if that optional integration is disabled."
        )
    elif not source_provenance_ok:
        remediation = (
            f"Set {marker_key} or SECRETS_SOURCE to a controlled secret source for "
            f"{target_config_key}, and rerun validation."
        )
    else:
        remediation = ""
    return {
        "key": marker_key,
        "status": status,
        "classification": "source_provenance_metadata",
        "set": bool(provenance["source_marker_present"]),
        "placeholder_like": invalid_optional_sensitive_value,
        "default_like": False,
        "too_short": False,
        "forbidden_value_shape": False,
        "missing_uri_credentials": False,
        **provenance,
        "target_config_key": target_config_key,
        "config_keys": config_keys,
        "alternative_config_keys": alternative_config_keys,
        **({"issue_code": issue_code} if issue_code else {}),
        "recommended_source": "SOPS or deployment secret store",
        "source_steps": [],
        "action_keys": [marker_key],
        "categories": ["secret_provenance"],
        "owners": ["deployment_operator"],
        "remediation": remediation,
    }


def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pass": sum(1 for item in checks if item.get("status") == "pass"),
        "fail": sum(1 for item in checks if item.get("status") == "fail"),
        "total": len(checks),
    }


def _next_actions(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in checks:
        if item.get("status") != "fail":
            continue
        key = str(item.get("key") or "")
        owners = item.get("owners") if isinstance(item.get("owners"), list) else []
        actions.append(
            {
                "key": key,
                "label": key,
                "status": "fail",
                "category": "secret_inventory_validation",
                "action": item.get("remediation")
                or f"Inject {key} through SOPS or the deployment secret store and rerun validation.",
                "owner": str(owners[0]) if owners else "deployment_operator",
                "can_auto_fix": False,
                "requires_secret_update": True,
                "production_required": True,
                "config_keys": item.get("config_keys") if isinstance(item.get("config_keys"), list) else [key],
                **(
                    {
                        "verification_commands": [
                            "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error",
                            "python scripts/validate_secret_inventory.py --env-file apps/backend/.env.prod --fail-on-error",
                        ]
                    }
                    if key == "env_file"
                    else {}
                ),
                **(
                    {"alternative_config_keys": item["alternative_config_keys"]}
                    if isinstance(item.get("alternative_config_keys"), list)
                    else {}
                ),
                **(
                    {"target_config_key": item["target_config_key"]}
                    if item.get("target_config_key")
                    else {}
                ),
                "verification": [
                    {
                        "kind": "script",
                        "target": "scripts/validate_secret_inventory.py",
                        "expectation": f"checks.{key}.status=pass",
                    }
                ],
            }
        )
    return actions


def _load_inventory(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Secret inventory must be a JSON object")
    return data


def validate_secret_inventory(
    inventory_path: Path | str,
    *,
    env_values: Mapping[str, str] | None = None,
    env_file: Path | str | None = None,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    destination = Path(out_dir) if out_dir is not None else root / "docs" / "verification"
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    inventory_file = Path(inventory_path)
    inventory = _load_inventory(inventory_file)
    if env_values is not None:
        values = {str(key): str(value) for key, value in env_values.items()}
        source = "provided_mapping"
        env_path: str | None = None
        env_file_check: dict[str, Any] | None = None
    elif env_file is not None:
        env_path_obj = Path(env_file)
        source = "env_file"
        env_path = str(env_path_obj)
        if env_path_obj.exists():
            values = parse_env_file(env_path_obj)
            env_file_check = None
        else:
            values = {}
            env_file_check = {
                "key": "env_file",
                "classification": "configuration",
                "status": "fail",
                "env_path": str(env_path_obj),
                "set": False,
                "owners": ["deployment_operator"],
                "config_keys": [],
                "remediation": (
                    "Materialize the approved apps/backend/.env.prod candidate from SOPS or the deployment "
                    "secret store, validate it, then rerun secret inventory validation."
                ),
            }
    else:
        values = {str(key): str(value) for key, value in os.environ.items()}
        source = "current_environment"
        env_path = None
        env_file_check = None
    inventory_items = _inventory_items(inventory)
    item_keys = {str(item.get("key") or "").strip() for item in inventory_items}
    checks: list[dict[str, Any]] = []
    if env_file_check is not None:
        checks.append(env_file_check)
    else:
        checks.extend(
            _check_inventory_item(item, env_values=values, specs=_production_spec_by_key())
            for item in inventory_items
        )
    source_provenance_target_config_keys = _inventory_source_provenance_target_config_keys(inventory)
    if env_file_check is None:
        for target_config_key in source_provenance_target_config_keys:
            if target_config_key in item_keys:
                continue
            checks.append(
                _check_source_provenance_target(target_config_key, env_values=values)
            )
    target_issue_check = _source_provenance_target_config_key_issue_check(
        _invalid_source_provenance_target_config_keys(inventory)
    )
    if target_issue_check is not None:
        checks.append(target_issue_check)
    self_consistency_check = _inventory_self_consistency_check(
        inventory,
        inventory_items=inventory_items,
    )
    if self_consistency_check is not None:
        checks.append(self_consistency_check)
    summary = _summary(checks)
    actions = _next_actions(checks)
    secret_config_keys = [
        item["key"]
        for item in checks
        if item.get("classification") not in {"source_provenance_metadata", "inventory_schema"}
    ]
    export_path = destination / f"secret-inventory-validation-{stamp}.json"
    latest_path = destination / "secret-inventory-validation-latest.json"
    report: dict[str, Any] = {
        "report_type": "secret_inventory_validation",
        "status": "pass" if summary["fail"] == 0 else "fail",
        "generated_at": timestamp.isoformat(),
        "source": source,
        "inventory_path": str(inventory_file),
        "env_path": env_path,
        "summary": summary,
        "secret_config_key_count": len(secret_config_keys),
        "secret_config_keys": secret_config_keys,
        "source_provenance_target_config_key_count": len(source_provenance_target_config_keys),
        "source_provenance_target_config_keys": source_provenance_target_config_keys,
        "checks": checks,
        "next_action_count": len(actions),
        "next_actions": actions,
        "export_path": str(export_path),
        "latest_path": str(latest_path),
        "policy_basis": [
            "OWASP Secrets Management: reports may show secret key names and validation status, never secret values.",
            "Twelve-Factor Config: deploy-specific config belongs in the environment or secret store.",
            "Project release gate: production secret inventory must validate before release approval.",
        ],
    }
    text = json.dumps(public_cli_result(report, project_root=root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return report


def validation_exit_code(report: dict[str, Any], *, fail_on_error: bool) -> int:
    if fail_on_error and report.get("status") != "pass":
        return 2
    return 0


def format_cli_json(report: dict[str, Any]) -> str:
    return json.dumps(public_cli_result(report), ensure_ascii=True, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a release gate secret inventory against injected env values.")
    parser.add_argument(
        "--inventory",
        default="docs/verification/release-gate-secret-inventory-latest.json",
        help="Secret inventory JSON generated by scripts/run_release_gate.py.",
    )
    parser.add_argument("--env-file", default=None, help="Optional production env file to validate instead of os.environ.")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    report = validate_secret_inventory(
        Path(args.inventory),
        env_file=Path(args.env_file) if args.env_file else None,
        out_dir=Path(args.out_dir) if args.out_dir else None,
    )
    print(format_cli_json(report))
    return validation_exit_code(report, fail_on_error=args.fail_on_error)


if __name__ == "__main__":
    raise SystemExit(main())
