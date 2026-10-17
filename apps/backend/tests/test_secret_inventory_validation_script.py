from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "validate_secret_inventory.py"
    spec = importlib.util.spec_from_file_location("validate_secret_inventory", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_inventory(
    path: Path,
    keys: list[str],
    *,
    source_provenance_target_config_keys: list[str] | None = None,
) -> None:
    items = []
    for key in keys:
        items.append(
            {
                "key": key,
                "classification": "credential_bearing_uri" if key == "DATABASE_URL" else "secret",
                "recommended_source": "SOPS or deployment secret store",
                "source_steps": ["production_env_validation"],
                "action_keys": [key],
                "categories": ["database" if key == "DATABASE_URL" else "secrets"],
                "owners": ["deployment_operator"],
                "verification": [
                    {
                        "kind": "script",
                        "target": "scripts/validate_secret_inventory.py",
                        "expectation": f"checks.{key}.status=pass",
                    }
                ],
            }
        )
    path.write_text(
        json.dumps(
            {
                "report_type": "release_gate_secret_inventory",
                "status": "required",
                "secret_config_keys": keys,
                "source_provenance_target_config_keys": source_provenance_target_config_keys or [],
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_secret_inventory_validation_writes_sanitized_report_from_env_values(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(
        inventory_path,
        ["APP_API_KEY", "APP_SECRET_KEY", "DATABASE_URL", "MINIO_SECRET_KEY", "SENTRY_DSN"],
    )
    env_values = {
        "APP_API_KEY": "a" * 32,
        "APP_API_KEY_SOURCE": "sops:APP_API_KEY",
        "APP_SECRET_KEY": "b" * 40,
        "APP_SECRET_KEY_SOURCE": "sops:APP_SECRET_KEY",
        "DATABASE_URL": "postgresql://agent_user:db-pass@postgres:5432/agent_db",
        "DATABASE_URL_SOURCE": "vault:DATABASE_URL",
        "MINIO_SECRET_KEY": "minioadmin",
        "SENTRY_DSN": "<REQUIRED_SENTRY_DSN_OR_DOCUMENTED_ALTERNATIVE>",
    }

    report = module.validate_secret_inventory(
        inventory_path,
        env_values=env_values,
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 13, 40, tzinfo=timezone.utc),
    )

    latest_path = Path(report["latest_path"])
    exported = json.loads(latest_path.read_text(encoding="utf-8"))
    rendered = json.dumps(exported, ensure_ascii=False)
    checks = {item["key"]: item for item in exported["checks"]}

    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 3, "fail": 2, "total": 5}
    assert exported["report_type"] == "secret_inventory_validation"
    assert exported["status"] == "fail"
    assert exported["source"] == "provided_mapping"
    assert exported["inventory_path"] == "docs/verification/release-gate-secret-inventory-latest.json"
    assert exported["export_path"] == "docs/verification/secret-inventory-validation-20260708T134000Z.json"
    assert exported["latest_path"] == "docs/verification/secret-inventory-validation-latest.json"
    assert checks["APP_API_KEY"]["status"] == "pass"
    assert checks["APP_API_KEY"]["set"] is True
    assert checks["APP_API_KEY"]["too_short"] is False
    assert checks["APP_API_KEY"]["source_provenance_ok"] is True
    assert checks["APP_API_KEY"]["source_marker_key"] == "APP_API_KEY_SOURCE"
    assert checks["APP_API_KEY"]["source_marker_family"] == "sops"
    assert checks["DATABASE_URL"]["status"] == "pass"
    assert checks["DATABASE_URL"]["source_provenance_ok"] is True
    assert checks["DATABASE_URL"]["source_marker_family"] == "vault"
    assert checks["DATABASE_URL"]["uri"] == {
        "scheme": "postgresql",
        "host_set": True,
        "port_set": True,
        "contains_credentials": True,
    }
    assert checks["MINIO_SECRET_KEY"]["status"] == "fail"
    assert checks["MINIO_SECRET_KEY"]["default_like"] is True
    assert checks["SENTRY_DSN"]["status"] == "fail"
    assert checks["SENTRY_DSN"]["placeholder_like"] is True
    assert [item["key"] for item in exported["next_actions"]] == ["MINIO_SECRET_KEY", "SENTRY_DSN"]
    assert module.validation_exit_code(report, fail_on_error=True) == 2
    assert "db-pass" not in rendered
    assert "a" * 32 not in rendered
    assert "b" * 40 not in rendered
    assert "minioadmin" not in rendered
    assert "<REQUIRED" not in rendered
    assert "sops:APP_API_KEY" not in rendered
    assert "vault:DATABASE_URL" not in rendered
    assert "E:\\" not in rendered
    assert "E:/" not in rendered


def test_secret_inventory_validation_writes_report_when_env_file_missing(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(inventory_path, ["APP_API_KEY", "APP_SECRET_KEY"])
    missing_env = tmp_path / "apps" / "backend" / ".env.prod"

    report = module.validate_secret_inventory(
        inventory_path,
        env_file=missing_env,
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 9, 8, 55, tzinfo=timezone.utc),
    )

    latest_path = Path(report["latest_path"])
    exported = json.loads(latest_path.read_text(encoding="utf-8"))
    rendered = json.dumps(exported, ensure_ascii=False)

    assert exported["status"] == "fail"
    assert exported["source"] == "env_file"
    assert exported["env_path"] == "apps/backend/.env.prod"
    assert exported["summary"]["fail"] >= 1
    assert exported["checks"][0]["key"] == "env_file"
    assert exported["checks"][0]["status"] == "fail"
    assert exported["checks"][0]["env_path"] == "apps/backend/.env.prod"
    assert [item["key"] for item in exported["checks"]] == ["env_file"]
    assert [item["key"] for item in exported["next_actions"]] == ["env_file"]
    assert exported["next_actions"][0]["key"] == "env_file"
    assert exported["next_actions"][0]["action"] == (
        "Materialize the approved apps/backend/.env.prod candidate from SOPS or the deployment "
        "secret store, validate it, then rerun secret inventory validation."
    )
    assert (
        "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error"
        in exported["next_actions"][0]["verification_commands"]
    )
    assert (
        "python scripts/validate_secret_inventory.py --env-file apps/backend/.env.prod --fail-on-error"
        in exported["next_actions"][0]["verification_commands"]
    )
    assert "FileNotFoundError" not in rendered
    assert str(tmp_path) not in rendered
    assert module.validation_exit_code(report, fail_on_error=True) == 2


def test_secret_inventory_validation_passes_when_all_required_keys_are_safe(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(inventory_path, ["APP_API_KEY", "APP_SECRET_KEY", "DATABASE_URL"])

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "APP_API_KEY": "api_" + "x" * 28,
            "APP_API_KEY_SOURCE": "sops:APP_API_KEY",
            "APP_SECRET_KEY": "secret_" + "y" * 36,
            "APP_SECRET_KEY_SOURCE": "sops:APP_SECRET_KEY",
            "DATABASE_URL": "postgresql://agent_user:strong-db-secret@postgres:5432/agent_db",
            "DATABASE_URL_SOURCE": "kubernetes-secret:DATABASE_URL",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 13, 45, tzinfo=timezone.utc),
    )

    assert report["status"] == "pass"
    assert report["summary"] == {"pass": 3, "fail": 0, "total": 3}
    assert report["next_actions"] == []
    assert module.validation_exit_code(report, fail_on_error=True) == 0


def test_secret_inventory_validation_requires_trusted_source_provenance(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(inventory_path, ["APP_API_KEY", "APP_SECRET_KEY"])

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "APP_API_KEY": "api_" + "x" * 28,
            "APP_SECRET_KEY": "secret_" + "y" * 36,
            "APP_SECRET_KEY_SOURCE": "plain-env:manual-copy",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 14, 5, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}
    rendered = Path(report["latest_path"]).read_text(encoding="utf-8")

    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 0, "fail": 2, "total": 2}
    assert checks["APP_API_KEY"]["source_marker_present"] is False
    assert checks["APP_API_KEY"]["source_provenance_ok"] is False
    assert checks["APP_API_KEY"]["status"] == "fail"
    assert checks["APP_SECRET_KEY"]["source_marker_present"] is True
    assert checks["APP_SECRET_KEY"]["source_marker_key"] == "APP_SECRET_KEY_SOURCE"
    assert checks["APP_SECRET_KEY"]["source_marker_family"] == "untrusted"
    assert checks["APP_SECRET_KEY"]["source_provenance_ok"] is False
    assert [item["key"] for item in report["next_actions"]] == ["APP_API_KEY", "APP_SECRET_KEY"]
    assert all("controlled secret source" in item["action"] for item in report["next_actions"])
    assert "plain-env:manual-copy" not in rendered


def test_secret_inventory_validation_checks_non_secret_source_provenance_targets(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(
        inventory_path,
        [],
        source_provenance_target_config_keys=["REDIS_URL", "RATE_LIMIT_STORAGE_URI"],
    )

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "REDIS_URL_SOURCE": "sops:REDIS_URL",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 14, 20, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}
    rendered = Path(report["latest_path"]).read_text(encoding="utf-8")

    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 1, "fail": 1, "total": 2}
    assert report["source_provenance_target_config_keys"] == [
        "REDIS_URL",
        "RATE_LIMIT_STORAGE_URI",
    ]
    assert checks["REDIS_URL_SOURCE"]["status"] == "pass"
    assert checks["REDIS_URL_SOURCE"]["target_config_key"] == "REDIS_URL"
    assert checks["REDIS_URL_SOURCE"]["source_marker_key"] == "REDIS_URL_SOURCE"
    assert checks["REDIS_URL_SOURCE"]["source_marker_family"] == "sops"
    assert checks["RATE_LIMIT_STORAGE_URI_SOURCE"]["status"] == "fail"
    assert checks["RATE_LIMIT_STORAGE_URI_SOURCE"]["target_config_key"] == "RATE_LIMIT_STORAGE_URI"
    assert checks["RATE_LIMIT_STORAGE_URI_SOURCE"]["source_marker_present"] is False
    assert checks["RATE_LIMIT_STORAGE_URI_SOURCE"]["config_keys"] == [
        "RATE_LIMIT_STORAGE_URI_SOURCE"
    ]
    assert checks["RATE_LIMIT_STORAGE_URI_SOURCE"]["alternative_config_keys"] == [
        "SECRETS_SOURCE"
    ]
    assert [item["key"] for item in report["next_actions"]] == [
        "RATE_LIMIT_STORAGE_URI_SOURCE"
    ]
    assert report["next_actions"][0]["config_keys"] == ["RATE_LIMIT_STORAGE_URI_SOURCE"]
    assert report["next_actions"][0]["alternative_config_keys"] == ["SECRETS_SOURCE"]
    assert "sops:REDIS_URL" not in rendered


def test_secret_inventory_validation_accepts_global_source_for_source_targets(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(
        inventory_path,
        [],
        source_provenance_target_config_keys=["REDIS_URL", "RATE_LIMIT_STORAGE_URI"],
    )

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "SECRETS_SOURCE": "sops:production",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 14, 25, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}

    assert report["status"] == "pass"
    assert report["summary"] == {"pass": 2, "fail": 0, "total": 2}
    assert checks["REDIS_URL_SOURCE"]["source_marker_key"] == "SECRETS_SOURCE"
    assert checks["RATE_LIMIT_STORAGE_URI_SOURCE"]["source_marker_key"] == "SECRETS_SOURCE"
    assert report["next_actions"] == []


def test_secret_inventory_validation_rejects_stale_optional_source_targets(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(
        inventory_path,
        [],
        source_provenance_target_config_keys=["FRED_API_KEY"],
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory.update(
        {
            "secret_config_key_count": 0,
            "source_provenance_key": "SECRETS_SOURCE",
            "source_provenance_keys": ["SECRETS_SOURCE", "FRED_API_KEY_SOURCE"],
            "source_provenance_key_count": 2,
            "per_key_source_provenance_keys": ["FRED_API_KEY_SOURCE"],
            "per_key_source_provenance_key_count": 1,
            "source_provenance_target_config_key_count": 1,
        }
    )
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "FRED_API_KEY_SOURCE": "sops:FRED_API_KEY",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 14, 30, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}
    rendered = Path(report["latest_path"]).read_text(encoding="utf-8")

    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 0, "fail": 1, "total": 1}
    assert checks["FRED_API_KEY_SOURCE"]["status"] == "fail"
    assert checks["FRED_API_KEY_SOURCE"]["issue_code"] == "stale_optional_source_metadata"
    assert checks["FRED_API_KEY_SOURCE"]["target_config_key"] == "FRED_API_KEY"
    assert [item["key"] for item in report["next_actions"]] == ["FRED_API_KEY_SOURCE"]
    assert report["next_actions"][0]["config_keys"] == ["FRED_API_KEY_SOURCE"]
    assert report["next_actions"][0]["alternative_config_keys"] == ["FRED_API_KEY"]
    assert "sops:FRED_API_KEY" not in rendered


def test_secret_inventory_validation_rejects_placeholder_optional_source_target_values(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(
        inventory_path,
        [],
        source_provenance_target_config_keys=["COINGECKO_API_KEY"],
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory.update(
        {
            "secret_config_key_count": 0,
            "source_provenance_key": "SECRETS_SOURCE",
            "source_provenance_keys": ["SECRETS_SOURCE", "COINGECKO_API_KEY_SOURCE"],
            "source_provenance_key_count": 2,
            "per_key_source_provenance_keys": ["COINGECKO_API_KEY_SOURCE"],
            "per_key_source_provenance_key_count": 1,
            "source_provenance_target_config_key_count": 1,
        }
    )
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "SECRETS_SOURCE": "sops:production",
            "COINGECKO_API_KEY": "<REQUIRED_COINGECKO_API_KEY>",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 9, 1, 10, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}
    rendered = Path(report["latest_path"]).read_text(encoding="utf-8")

    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 0, "fail": 1, "total": 1}
    assert checks["COINGECKO_API_KEY_SOURCE"]["status"] == "fail"
    assert checks["COINGECKO_API_KEY_SOURCE"]["placeholder_like"] is True
    assert checks["COINGECKO_API_KEY_SOURCE"]["issue_code"] == "invalid_optional_sensitive_value"
    assert checks["COINGECKO_API_KEY_SOURCE"]["target_config_key"] == "COINGECKO_API_KEY"
    assert [item["key"] for item in report["next_actions"]] == ["COINGECKO_API_KEY_SOURCE"]
    assert report["next_actions"][0]["config_keys"] == ["COINGECKO_API_KEY"]
    assert report["next_actions"][0]["alternative_config_keys"] == ["COINGECKO_API_KEY_SOURCE"]
    assert "REQUIRED_COINGECKO_API_KEY" not in rendered
    assert "sops:production" not in rendered


def test_secret_inventory_validation_rejects_invalid_source_provenance_target_keys(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(
        inventory_path,
        [],
        source_provenance_target_config_keys=[
            "REDIS_URL",
            "REDIS_URL_SOURCE",
            "UNKNOWN_RUNTIME_KEY",
            "SECRETS_SOURCE",
        ],
    )

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "SECRETS_SOURCE": "sops:production",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 14, 35, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}
    rendered = Path(report["latest_path"]).read_text(encoding="utf-8")

    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 1, "fail": 1, "total": 2}
    assert report["source_provenance_target_config_keys"] == ["REDIS_URL"]
    assert checks["REDIS_URL_SOURCE"]["status"] == "pass"
    assert checks["inventory_source_provenance_target_config_keys"]["status"] == "fail"
    assert checks["inventory_source_provenance_target_config_keys"]["issue_code"] == (
        "invalid_source_provenance_target_config_keys"
    )
    assert checks["inventory_source_provenance_target_config_keys"][
        "invalid_source_provenance_target_config_keys"
    ] == ["REDIS_URL_SOURCE", "UNKNOWN_RUNTIME_KEY", "SECRETS_SOURCE"]
    assert [item["key"] for item in report["next_actions"]] == [
        "inventory_source_provenance_target_config_keys"
    ]
    assert "sops:production" not in rendered


def test_secret_inventory_validation_rejects_inconsistent_inventory_metadata(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(inventory_path, ["APP_API_KEY", "DATABASE_URL"])
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory.update(
        {
            "secret_config_key_count": 99,
            "source_provenance_key": "APP_API_KEY_SOURCE",
            "source_provenance_key_count": 3,
            "source_provenance_keys": ["SECRETS_SOURCE", "APP_API_KEY_SOURCE"],
            "per_key_source_provenance_key_count": 2,
            "per_key_source_provenance_keys": ["APP_API_KEY_SOURCE"],
        }
    )
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "SECRETS_SOURCE": "sops:production",
            "APP_API_KEY": "api_" + "x" * 28,
            "DATABASE_URL": "postgresql://agent_user:strong-db-secret@postgres:5432/agent_db",
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 14, 45, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}
    rendered = Path(report["latest_path"]).read_text(encoding="utf-8")

    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 2, "fail": 1, "total": 3}
    assert report["secret_config_keys"] == ["APP_API_KEY", "DATABASE_URL"]
    assert checks["APP_API_KEY"]["status"] == "pass"
    assert checks["DATABASE_URL"]["status"] == "pass"
    assert checks["inventory_self_consistency"]["status"] == "fail"
    assert checks["inventory_self_consistency"]["issue_codes"] == [
        "secret_config_key_count_mismatch",
        "source_provenance_key_invalid",
        "source_provenance_key_count_mismatch",
        "missing_secret_source_provenance_keys",
        "per_key_source_provenance_key_count_mismatch",
    ]
    assert checks["inventory_self_consistency"]["missing_secret_source_provenance_keys"] == [
        "DATABASE_URL_SOURCE"
    ]
    assert [item["key"] for item in report["next_actions"]] == ["inventory_self_consistency"]
    assert "sops:production" not in rendered
    assert "strong-db-secret" not in rendered


def test_secret_inventory_validation_rejects_invalid_source_provenance_keys(tmp_path):
    module = _load_module()
    out_dir = tmp_path / "docs" / "verification"
    inventory_path = out_dir / "release-gate-secret-inventory-latest.json"
    out_dir.mkdir(parents=True)
    _write_inventory(
        inventory_path,
        ["APP_API_KEY"],
        source_provenance_target_config_keys=["REDIS_URL"],
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory.update(
        {
            "secret_config_key_count": 1,
            "source_provenance_key": "SECRETS_SOURCE",
            "source_provenance_keys": [
                "SECRETS_SOURCE",
                "APP_API_KEY_SOURCE",
                "UNKNOWN_SOURCE",
                "BROKEN",
            ],
            "source_provenance_key_count": 4,
            "per_key_source_provenance_keys": [
                "APP_API_KEY_SOURCE",
                "UNKNOWN_SOURCE",
                "BROKEN",
            ],
            "per_key_source_provenance_key_count": 3,
        }
    )
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    report = module.validate_secret_inventory(
        inventory_path,
        env_values={
            "SECRETS_SOURCE": "sops:production",
            "APP_API_KEY": "api_" + "x" * 28,
        },
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 14, 55, tzinfo=timezone.utc),
    )

    checks = {item["key"]: item for item in report["checks"]}
    rendered = Path(report["latest_path"]).read_text(encoding="utf-8")

    assert report["status"] == "fail"
    assert checks["APP_API_KEY"]["status"] == "pass"
    assert checks["REDIS_URL_SOURCE"]["status"] == "pass"
    assert checks["inventory_self_consistency"]["status"] == "fail"
    assert checks["inventory_self_consistency"]["issue_codes"] == [
        "invalid_source_provenance_keys",
        "missing_target_source_provenance_keys",
    ]
    assert checks["inventory_self_consistency"]["invalid_source_provenance_keys"] == [
        "UNKNOWN_SOURCE",
        "BROKEN",
    ]
    assert checks["inventory_self_consistency"]["missing_target_source_provenance_keys"] == [
        "REDIS_URL_SOURCE"
    ]
    assert [item["key"] for item in report["next_actions"]] == ["inventory_self_consistency"]
    assert "sops:production" not in rendered


def test_secret_inventory_validation_reuses_production_env_source_policy():
    module = _load_module()
    production_env_module = sys.modules["validate_production_env"]

    assert (
        module.ACCEPTED_SECRET_SOURCE_PREFIXES
        is production_env_module.ACCEPTED_SECRET_SOURCE_PREFIXES
    )
    assert module._source_family is production_env_module._source_family
    assert module._trusted_source_provenance is production_env_module._trusted_source_provenance
