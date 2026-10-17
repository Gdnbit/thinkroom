from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _source_provenance_runbook_fixture(tmp_path):
    runbook = tmp_path / "docs" / "operations" / "PRODUCTION_SECRET_SOURCE_PROVENANCE.md"
    runbook.parent.mkdir(parents=True, exist_ok=True)
    runbook.write_text("# Production Secret Source Provenance Runbook\n", encoding="utf-8")


def _load_gate_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "run_release_gate.py"
    spec = importlib.util.spec_from_file_location("run_release_gate", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_release_gate_redaction_preserves_documented_redis_uri_examples():
    module = _load_gate_module()
    fake_openrouter_key = "sk-or-v1-" + "should-not-be-read"
    result = {
        "required_actions": [
            {
                "key": "rate_limit_storage",
                "action": (
                    "Set RATE_LIMIT_STORAGE_URI to a shared Redis URI such as redis://redis:6379/1, "
                    "enable REDIS_ENABLED=true, and rerun the production preflight."
                ),
            }
        ],
        "openrouter_key": fake_openrouter_key,
        "source_marker": "sops:OPENROUTER_KEY",
        "historical_leak": "db-secret",
    }

    public_result = module.public_cli_result(result)
    rendered = json.dumps(public_result, ensure_ascii=False)

    action = public_result["required_actions"][0]["action"]
    assert "redis://redis:6379/1" in action
    assert "Set RATE_LIMIT_STORAGE_URI" in action
    assert not action.startswith("1, enable REDIS_ENABLED=true")
    assert fake_openrouter_key not in rendered
    assert "sops:OPENROUTER_KEY" not in rendered
    assert "db-secret" not in rendered


def test_release_gate_runs_scan_before_preflight_and_fails_when_not_ready(tmp_path):
    module = _load_gate_module()
    project_root = tmp_path
    out_dir = tmp_path / "docs" / "verification"
    calls: list[str] = []

    def fake_scan(**_kwargs):
        calls.append("scan")
        return {
            "export_path": str(out_dir / "public-api-leak-scan-20260708T061704Z.json"),
            "latest_path": str(out_dir / "public-api-leak-scan-latest.json"),
            "status": "pass",
            "leak_count": "0",
        }

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {
            "export_path": str(out_dir / "production-preflight-20260708T061703Z.json"),
            "latest_path": str(out_dir / "production-preflight-latest.json"),
            "status": "warn",
            "production_ready": False,
            "local_demo_ready": True,
            "next_actions": [
                {
                    "key": "secrets",
                    "label": "密钥与认证",
                    "status": "warn",
                    "owner": "deployment_operator",
                    "config_keys": ["APP_API_KEY", "APP_SECRET_KEY"],
                    "verification": [{"kind": "endpoint", "target": "/api/v1/health/production-secrets-tls"}],
                },
                {
                    "key": "object_storage",
                    "label": "对象存储",
                    "status": "warn",
                    "owner": "deployment_operator",
                    "config_keys": ["MINIO_SECURE"],
                    "verification": [{"kind": "endpoint", "target": "/api/v1/health/release-readiness"}],
                    "verification_commands": ["python existing-check.py"],
                },
            ],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=out_dir,
        project_root=project_root,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )
    public_result = module.public_cli_result(result)
    rendered = json.dumps(public_result, ensure_ascii=False)

    assert calls == ["scan", "preflight"]
    assert result["status"] == "blocked"
    assert result["gate_exit_code"] == 2
    assert result["exit_code"] == 2
    assert result["cli_exit_code"] == 2
    assert result["run_mode"] == "strict"
    assert result["release_allowed"] is False
    assert result["blocking_steps"] == ["production_preflight"]
    assert result["required_action_count"] == 2
    assert [item["key"] for item in result["required_actions"]] == ["secrets", "object_storage"]
    assert {item["source_step"] for item in result["required_actions"]} == {"production_preflight"}
    assert result["required_actions"][0]["verification_commands"] == [
        "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000"
    ]
    assert result["required_actions"][1]["verification_commands"] == ["python existing-check.py"]
    action_plan_text = (out_dir / "release-gate-action-plan-latest.md").read_text(encoding="utf-8")
    assert (
        "Verification commands: `python scripts/run_release_gate.py --require-env-file "
        "--env-file apps/backend/.env.prod --backend-url [backend-url]`"
    ) in action_plan_text
    assert [step["key"] for step in result["steps"]] == [
        "public_api_leak_scan",
        "production_preflight",
        "verification_report_hygiene",
    ]
    assert public_result["steps"][0]["label"] == "Public API leak scan"
    assert public_result["steps"][1]["label"] == "Production preflight"
    assert public_result["steps"][2]["label"] == "Verification report hygiene"
    assert public_result["steps"][0]["latest_path"] == "docs/verification/public-api-leak-scan-latest.json"
    assert public_result["steps"][1]["latest_path"] == "docs/verification/production-preflight-latest.json"
    assert "E:\\" not in rendered
    assert "E:/" not in rendered
    assert "127.0.0.1" not in rendered


def test_release_gate_blocks_when_verification_report_hygiene_fails(tmp_path):
    module = _load_gate_module()
    project_root = tmp_path
    out_dir = tmp_path / "docs" / "verification"
    calls: list[str] = []

    def fake_scan(**_kwargs):
        calls.append("scan")
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {"status": "pass", "production_ready": True, "next_actions": []}

    def fake_hygiene(report_dir, **_kwargs):
        calls.append(f"hygiene:{Path(report_dir).name}")
        return {
            "report_type": "verification_report_hygiene",
            "status": "fail",
            "checked_count": 2,
            "issue_count": 1,
            "issues": [
                {
                    "path": "bad-latest.json",
                    "code": "invalid_json",
                    "message": "Verification report is not strict JSON.",
                }
            ],
            "next_actions": [
                {
                    "key": "verification_report_hygiene",
                    "label": "Verification report hygiene",
                    "status": "fail",
                    "category": "verification_artifacts",
                    "action": "Fix invalid verification reports and rerun scripts/check_verification_reports.py.",
                    "owner": "release_operator",
                }
            ],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=out_dir,
        project_root=project_root,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
        report_hygiene_checker=fake_hygiene,
    )

    assert calls == ["scan", "preflight", "hygiene:verification"]
    assert result["status"] == "blocked"
    assert result["blocking_steps"] == ["verification_report_hygiene"]
    assert [step["key"] for step in result["steps"]] == [
        "public_api_leak_scan",
        "production_preflight",
        "verification_report_hygiene",
    ]
    assert result["required_actions"][0]["source_step"] == "verification_report_hygiene"
    assert result["required_actions"][0]["key"] == "verification_report_hygiene"


def test_release_gate_turns_step_exceptions_into_blocking_actions_and_reports(tmp_path):
    module = _load_gate_module()
    out_dir = tmp_path / "docs" / "verification"
    calls: list[str] = []

    def fake_scan(**_kwargs):
        calls.append("scan")
        raise RuntimeError("network timeout with local details")

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {"status": "pass", "production_ready": True, "local_demo_ready": False}

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=out_dir,
        project_root=tmp_path,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    action_plan = Path(result["action_plan_latest_path"]).read_text(encoding="utf-8")
    exported = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))

    assert calls == ["scan", "preflight"]
    assert result["status"] == "blocked"
    assert result["release_allowed"] is False
    assert result["blocking_steps"] == ["public_api_leak_scan"]
    assert result["step_exception_count"] == 1
    assert result["execution_failure_steps"] == ["public_api_leak_scan"]
    assert [step["key"] for step in result["steps"]] == [
        "public_api_leak_scan",
        "production_preflight",
        "verification_report_hygiene",
    ]
    assert result["steps"][0]["status"] == "fail"
    assert result["steps"][0]["exception_type"] == "RuntimeError"
    assert result["required_actions"][0]["source_step"] == "public_api_leak_scan"
    assert result["required_actions"][0]["key"] == "public_api_leak_scan_execution"
    assert result["required_actions"][0]["category"] == "release_gate_execution"
    assert exported["step_exception_count"] == 1
    assert exported["execution_failure_steps"] == ["public_api_leak_scan"]
    assert "network timeout" not in json.dumps(exported, ensure_ascii=False)
    assert "network timeout" not in action_plan
    assert "## Execution Failures" in action_plan
    assert "- `public_api_leak_scan`" in action_plan
    assert "- [ ] `public_api_leak_scan_execution`" in action_plan
    assert "- `public_api_leak_scan`: status=fail, exit_code=2" in action_plan


def test_release_gate_report_only_keeps_gate_result_but_exits_zero(tmp_path):
    module = _load_gate_module()

    def fake_scan(**_kwargs):
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        return {
            "status": "warn",
            "production_ready": False,
            "local_demo_ready": True,
            "next_actions": [
                {
                    "key": "secrets",
                    "label": "密钥与认证",
                    "status": "warn",
                    "owner": "deployment_operator",
                }
            ],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        report_only=True,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    assert result["status"] == "blocked"
    assert result["gate_exit_code"] == 2
    assert result["exit_code"] == 0
    assert result["cli_exit_code"] == 0
    assert result["run_mode"] == "report_only"
    assert result["release_allowed"] is False
    assert result["blocking_steps"] == ["production_preflight"]
    assert result["required_action_count"] == 1
    assert result["required_actions"][0]["source_step"] == "production_preflight"


def test_release_gate_cli_json_is_ascii_safe_for_windows_stdout():
    module = _load_gate_module()

    rendered = module.format_cli_json({"label": "密钥与认证", "status": "warn"})

    assert "密钥与认证" not in rendered
    assert "\\u5bc6\\u94a5" in rendered
    assert json.loads(rendered)["label"] == "密钥与认证"


def test_release_gate_can_run_production_env_validation_before_runtime_steps(tmp_path):
    module = _load_gate_module()
    calls: list[str] = []
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_env(_env_file, **_kwargs):
        calls.append("env")
        return {
            "status": "fail",
            "release_allowed": False,
            "env_file_issue_count": 1,
            "env_file_issue_codes": ["env_file_syntax_error"],
            "next_actions": [
                {
                    "key": "APP_API_KEY",
                    "label": "APP_API_KEY",
                    "status": "fail",
                    "owner": "deployment_operator",
                    "config_keys": ["APP_API_KEY"],
                }
            ],
        }

    def fake_scan(**_kwargs):
        calls.append("scan")
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {"status": "pass", "production_ready": True, "local_demo_ready": True}

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        env_file=env_file,
        env_exporter=fake_env,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )
    action_plan = Path(result["action_plan_latest_path"]).read_text(encoding="utf-8")

    assert calls == ["env", "scan", "preflight"]
    assert result["status"] == "blocked"
    assert result["gate_exit_code"] == 2
    assert result["env_file_issue_count"] == 1
    assert result["env_file_issue_codes"] == ["env_file_syntax_error"]
    assert result["blocking_steps"] == ["production_env_validation"]
    assert [step["key"] for step in result["steps"]] == [
        "production_env_validation",
        "public_api_leak_scan",
        "production_preflight",
        "verification_report_hygiene",
    ]
    assert result["required_actions"][0]["source_step"] == "production_env_validation"
    assert result["required_actions"][0]["key"] == "APP_API_KEY"
    assert "Env file issue count: 1" in action_plan
    assert "Env file issue codes: `env_file_syntax_error`" in action_plan


def test_release_gate_can_run_compose_config_check_after_env_validation(tmp_path):
    module = _load_gate_module()
    calls: list[str] = []
    env_file = tmp_path / ".env.production"
    env_file.write_text("DEPLOYMENT_CONFIG_ID=prod-compose\n", encoding="utf-8")

    def fake_env(_env_file, **_kwargs):
        calls.append("env")
        return {
            "status": "pass",
            "release_allowed": True,
            "deployment_config_id": "prod-compose",
            "next_actions": [],
        }

    def fake_compose(**kwargs):
        calls.append("compose")
        assert kwargs["env_file"] == env_file
        return {
            "status": "pass",
            "compose_config_ok": True,
            "exit_code": 0,
            "export_path": str(tmp_path / "docs" / "verification" / "production-compose-config-20260708T130200Z.json"),
            "latest_path": str(tmp_path / "docs" / "verification" / "production-compose-config-latest.json"),
            "next_actions": [],
        }

    def fake_scan(**_kwargs):
        calls.append("scan")
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {
            "status": "pass",
            "production_ready": True,
            "local_demo_ready": False,
            "runtime_config_identity": {
                "deployment_config_id": "prod-compose",
                "set": True,
            },
            "next_actions": [],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        env_file=env_file,
        env_exporter=fake_env,
        compose_config_checker=fake_compose,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    assert calls == ["env", "compose", "scan", "preflight"]
    assert result["status"] == "pass"
    assert [step["key"] for step in result["steps"]] == [
        "production_env_validation",
        "production_compose_config",
        "public_api_leak_scan",
        "production_preflight",
        "runtime_config_binding",
        "verification_report_hygiene",
    ]
    assert result["steps"][1]["status"] == "pass"
    assert result["steps"][1]["compose_config_ok"] is True
    assert result["production_compose_config_path"] == str(
        tmp_path / "docs" / "verification" / "production-compose-config-20260708T130200Z.json"
    )
    assert result["production_compose_config_latest_path"] == str(
        tmp_path / "docs" / "verification" / "production-compose-config-latest.json"
    )
    public_result = module.public_cli_result(result)
    assert public_result["production_compose_config_path"] == (
        "docs/verification/production-compose-config-20260708T130200Z.json"
    )
    assert public_result["production_compose_config_latest_path"] == (
        "docs/verification/production-compose-config-latest.json"
    )
    action_plan = Path(result["action_plan_latest_path"]).read_text(encoding="utf-8")
    assert "Production compose config: docs/verification/production-compose-config-latest.json" in action_plan


def test_release_gate_secret_inventory_uses_only_active_source_provenance_targets(tmp_path):
    module = _load_gate_module()
    out_dir = tmp_path / "docs" / "verification"
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260709T010000Z",
                "APP_DEBUG=false",
                "DATABASE_URL="
                + "postgresql://agent_user"
                + ":db-secret"
                + "@postgres:5432/agent_db",
                "DATABASE_URL_SOURCE=vault:DATABASE_URL",
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                "APP_API_KEY_SOURCE=sops:APP_API_KEY",
                "APP_SECRET_KEY=prod-secret-key-that-is-longer-than-32-characters",
                "APP_SECRET_KEY_SOURCE=sops:APP_SECRET_KEY",
                "OPENROUTER_API_KEY=" + "sk-or-v1-" + "prodvalidtokenlongenough",
                "OPENROUTER_API_KEY_SOURCE=sops:OPENROUTER_API_KEY",
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "REDIS_URL_SOURCE=sops:REDIS_URL",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "RATE_LIMIT_STORAGE_URI_SOURCE=sops:RATE_LIMIT_STORAGE_URI",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_ENDPOINT_SOURCE=sops:MINIO_ENDPOINT",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_ACCESS_KEY_SOURCE=sops:MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_SECRET_KEY_SOURCE=sops:MINIO_SECRET_KEY",
                "MINIO_BUCKET=agent-files-prod",
                "MINIO_BUCKET_SOURCE=sops:MINIO_BUCKET",
                "LOG_FORMAT=json",
                "LOG_FORMAT_SOURCE=sops:LOG_FORMAT",
                "SENTRY_DSN=https://public@example.ingest.sentry.io/123",
                "SENTRY_DSN_SOURCE=sops:SENTRY_DSN",
                "COINGECKO_API_KEY=coingecko-key-that-must-not-leak",
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    def fake_scan(**_kwargs):
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        return {"status": "pass", "production_ready": True, "local_demo_ready": False, "next_actions": []}

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 13, 2, tzinfo=timezone.utc),
        env_file=env_file,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )
    inventory = json.loads(Path(result["secret_inventory_path"]).read_text(encoding="utf-8"))
    rendered = json.dumps(module.public_cli_result(result), ensure_ascii=False)

    assert result["status"] == "blocked"
    assert result["blocking_steps"] == ["production_env_validation"]
    assert result["required_actions"][0]["key"] == "SECRETS_SOURCE"
    assert inventory["source_provenance_target_config_keys"] == [
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
        "COINGECKO_API_KEY",
    ]
    assert "COINGECKO_API_KEY_SOURCE" in inventory["source_provenance_keys"]
    assert "FRED_API_KEY_SOURCE" not in inventory["source_provenance_keys"]
    assert "GOOGLE_AI_API_KEY_SOURCE" not in inventory["source_provenance_keys"]
    assert "coingecko-key-that-must-not-leak" not in rendered
    assert "db-secret" not in rendered
    assert "sk-or-v1-" not in rendered


def test_release_gate_treats_database_url_secret_update_as_secret_config_key(tmp_path):
    module = _load_gate_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_env(_env_file, **_kwargs):
        return {
            "status": "fail",
            "release_allowed": False,
            "next_actions": [
                {
                    "key": "DATABASE_URL",
                    "label": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "owner": "database_operator",
                    "requires_secret_update": True,
                    "config_keys": ["DATABASE_URL"],
                    "uri": {
                        "scheme": "",
                        "host_set": False,
                        "port_set": False,
                        "contains_credentials": False,
                    },
                }
            ],
        }

    def fake_scan(**_kwargs):
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        return {"status": "pass", "production_ready": True, "local_demo_ready": False, "next_actions": []}

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        env_file=env_file,
        env_exporter=fake_env,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )
    inventory = json.loads(Path(result["secret_inventory_path"]).read_text(encoding="utf-8"))

    assert result["status"] == "blocked"
    assert result["required_secret_config_keys"] == ["DATABASE_URL"]
    assert inventory["secret_config_keys"] == ["DATABASE_URL"]
    assert inventory["items"][0]["key"] == "DATABASE_URL"
    assert inventory["items"][0]["source_steps"] == ["production_env_validation"]
    assert inventory["items"][0]["categories"] == ["database"]
    assert inventory["items"][0]["owners"] == ["database_operator"]
    assert result["required_config_key_actions"][0]["secret_config_key"] is True


def test_release_gate_groups_required_actions_by_source_step_and_category(tmp_path):
    module = _load_gate_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_env(_env_file, **_kwargs):
        return {
            "status": "fail",
            "release_allowed": False,
            "next_actions": [
                {
                    "key": "APP_API_KEY",
                    "label": "APP_API_KEY",
                    "status": "fail",
                    "category": "secrets",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["APP_API_KEY"],
                },
                {
                    "key": "SECRETS_SOURCE",
                    "label": "SECRETS_SOURCE",
                    "status": "fail",
                    "category": "secret_provenance",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["SECRETS_SOURCE"],
                    "alternative_config_keys": ["APP_API_KEY_SOURCE", "DATABASE_URL_SOURCE"],
                    "source_provenance_target_config_keys": ["APP_API_KEY", "DATABASE_URL"],
                },
                {
                    "key": "DATABASE_URL",
                    "label": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "owner": "database_operator",
                    "requires_secret_update": False,
                    "config_keys": ["DATABASE_URL"],
                    "uri": {"contains_credentials": True},
                },
            ],
        }

    def fake_scan(**_kwargs):
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        return {
            "status": "warn",
            "production_ready": False,
            "local_demo_ready": True,
            "next_actions": [
                {
                    "key": "observability",
                    "label": "观测性",
                    "status": "warn",
                    "category": "observability",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["LOG_FORMAT", "SENTRY_DSN"],
                },
                {
                    "key": "production_dry_run",
                    "label": "生产配置干跑",
                    "status": "warn",
                    "category": "production_profile",
                    "owner": "deployment_operator",
                    "requires_secret_update": False,
                    "config_keys": ["APP_ENV", "APP_API_KEY", "DATABASE_URL"],
                }
            ],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        env_file=env_file,
        env_exporter=fake_env,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )
    action_plan = Path(result["action_plan_latest_path"]).read_text(encoding="utf-8")

    assert result["required_action_count"] == 5
    assert result["required_config_key_count"] == 6
    assert result["required_config_keys"] == [
        "APP_API_KEY",
        "SECRETS_SOURCE",
        "DATABASE_URL",
        "LOG_FORMAT",
        "SENTRY_DSN",
        "APP_ENV",
    ]
    assert result["direct_required_config_key_count"] == 5
    assert result["direct_required_config_keys"] == [
        "APP_API_KEY",
        "SECRETS_SOURCE",
        "DATABASE_URL",
        "LOG_FORMAT",
        "SENTRY_DSN",
    ]
    assert result["required_secret_config_key_count"] == 3
    assert result["required_secret_config_keys"] == ["APP_API_KEY", "DATABASE_URL", "SENTRY_DSN"]
    assert result["required_config_key_action_count"] == 6
    assert result["required_config_key_actions"] == [
        {
            "config_key": "APP_API_KEY",
            "source_steps": ["production_env_validation", "production_preflight"],
            "action_keys": ["APP_API_KEY", "production_dry_run"],
            "categories": ["secrets", "production_profile"],
            "owners": ["deployment_operator"],
            "requires_secret_update": True,
            "secret_config_key": True,
            "context_only": False,
            "verification": [],
        },
        {
            "config_key": "SECRETS_SOURCE",
            "source_steps": ["production_env_validation"],
            "action_keys": ["SECRETS_SOURCE"],
            "categories": ["secret_provenance"],
            "owners": ["deployment_operator"],
            "requires_secret_update": True,
            "secret_config_key": False,
            "context_only": False,
            "alternative_config_keys": ["APP_API_KEY_SOURCE", "DATABASE_URL_SOURCE"],
            "source_provenance_target_config_keys": ["APP_API_KEY", "DATABASE_URL"],
            "verification": [],
        },
        {
            "config_key": "DATABASE_URL",
            "source_steps": ["production_env_validation", "production_preflight"],
            "action_keys": ["DATABASE_URL", "production_dry_run"],
            "categories": ["database", "production_profile"],
            "owners": ["database_operator", "deployment_operator"],
            "requires_secret_update": True,
            "secret_config_key": True,
            "context_only": False,
            "verification": [],
        },
        {
            "config_key": "LOG_FORMAT",
            "source_steps": ["production_preflight"],
            "action_keys": ["observability"],
            "categories": ["observability"],
            "owners": ["deployment_operator"],
            "requires_secret_update": True,
            "secret_config_key": False,
            "context_only": False,
            "verification": [],
        },
        {
            "config_key": "SENTRY_DSN",
            "source_steps": ["production_preflight"],
            "action_keys": ["observability"],
            "categories": ["observability"],
            "owners": ["deployment_operator"],
            "requires_secret_update": True,
            "secret_config_key": True,
            "context_only": False,
            "verification": [],
        },
        {
            "config_key": "APP_ENV",
            "source_steps": ["production_preflight"],
            "action_keys": ["production_dry_run"],
            "categories": ["production_profile"],
            "owners": ["deployment_operator"],
            "requires_secret_update": False,
            "secret_config_key": False,
            "context_only": True,
            "verification": [],
        },
    ]
    assert [item["key"] for item in result["required_actions"]] == [
        "APP_API_KEY",
        "SECRETS_SOURCE",
        "DATABASE_URL",
        "observability",
        "production_dry_run",
    ]
    assert result["required_action_groups"] == [
        {
            "source_step": "production_env_validation",
            "label": "Production env validation",
            "status": "fail",
            "action_count": 3,
            "action_keys": ["APP_API_KEY", "SECRETS_SOURCE", "DATABASE_URL"],
            "categories": {"secrets": 1, "secret_provenance": 1, "database": 1},
            "owner_counts": {"deployment_operator": 2, "database_operator": 1},
            "config_keys": ["APP_API_KEY", "SECRETS_SOURCE", "DATABASE_URL"],
            "requires_secret_update_count": 3,
            "secret_config_keys": ["APP_API_KEY", "DATABASE_URL"],
        },
        {
            "source_step": "production_preflight",
            "label": "Production preflight",
            "status": "warn",
            "action_count": 2,
            "action_keys": ["observability", "production_dry_run"],
            "categories": {"observability": 1, "production_profile": 1},
            "owner_counts": {"deployment_operator": 2},
            "config_keys": ["LOG_FORMAT", "SENTRY_DSN", "APP_ENV", "APP_API_KEY", "DATABASE_URL"],
            "requires_secret_update_count": 1,
            "secret_config_keys": ["SENTRY_DSN"],
        },
    ]
    assert "Required config key count: 6" in action_plan
    assert "Required config keys: `APP_API_KEY`, `SECRETS_SOURCE`, `DATABASE_URL`, `LOG_FORMAT`, `SENTRY_DSN`, `APP_ENV`" in action_plan
    assert "Direct required config key count: 5" in action_plan
    assert "Direct required config keys: `APP_API_KEY`, `SECRETS_SOURCE`, `DATABASE_URL`, `LOG_FORMAT`, `SENTRY_DSN`" in action_plan
    assert "Required secret config key count: 3" in action_plan
    assert "Required secret config keys: `APP_API_KEY`, `DATABASE_URL`, `SENTRY_DSN`" in action_plan
    assert "Required config key action count: 6" in action_plan
    assert "## Source Provenance Runbook" in action_plan
    assert "docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md" in action_plan
    assert "`SECRETS_SOURCE`" in action_plan
    assert "`*_SOURCE`" in action_plan
    assert "## Required Config Key Checklist" in action_plan
    assert "- [ ] `APP_API_KEY`" in action_plan
    assert "  - Source steps: `production_env_validation`, `production_preflight`" in action_plan
    assert "  - Action keys: `APP_API_KEY`, `production_dry_run`" in action_plan
    assert "  - Secret update: true" in action_plan
    assert "- [ ] `SECRETS_SOURCE`" in action_plan
    assert "  - Secret config key: false" in action_plan
    assert "  - Alternative config keys: `APP_API_KEY_SOURCE`, `DATABASE_URL_SOURCE`" in action_plan
    assert "  - Source provenance target config keys: `APP_API_KEY`, `DATABASE_URL`" in action_plan
    assert "- [ ] `APP_ENV`" in action_plan
    assert "  - Context only: true" in action_plan


def test_release_gate_blocks_when_candidate_env_and_runtime_config_id_mismatch(tmp_path):
    module = _load_gate_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("DEPLOYMENT_CONFIG_ID=prod-candidate\n", encoding="utf-8")

    def fake_env(_env_file, **_kwargs):
        return {
            "status": "pass",
            "release_allowed": True,
            "deployment_config_id": "prod-candidate",
            "next_actions": [],
        }

    def fake_scan(**_kwargs):
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        return {
            "status": "pass",
            "production_ready": True,
            "local_demo_ready": False,
            "runtime_config_identity": {
                "deployment_config_id": "prod-running",
                "set": True,
            },
            "next_actions": [],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        env_file=env_file,
        env_exporter=fake_env,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    assert result["status"] == "blocked"
    assert result["release_allowed"] is False
    assert result["blocking_steps"] == ["runtime_config_binding"]
    assert [step["key"] for step in result["steps"]] == [
        "production_env_validation",
        "public_api_leak_scan",
        "production_preflight",
        "runtime_config_binding",
        "verification_report_hygiene",
    ]
    assert result["steps"][-2]["key"] == "runtime_config_binding"
    assert result["steps"][-2]["status"] == "fail"
    assert result["steps"][-1]["key"] == "verification_report_hygiene"
    assert result["required_actions"][0]["key"] == "runtime_config_binding"
    assert result["required_actions"][0]["category"] == "configuration"
    assert result["required_action_groups"][0]["source_step"] == "runtime_config_binding"


def test_release_gate_passes_when_candidate_env_and_runtime_config_id_match(tmp_path):
    module = _load_gate_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z\n", encoding="utf-8")

    def fake_env(_env_file, **_kwargs):
        return {
            "status": "pass",
            "release_allowed": True,
            "deployment_config_id": "prod-20260708T081500Z",
            "next_actions": [],
        }

    def fake_scan(**_kwargs):
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        return {
            "status": "pass",
            "production_ready": True,
            "local_demo_ready": False,
            "runtime_config_identity": {
                "deployment_config_id": "prod-20260708T081500Z",
                "set": True,
            },
            "next_actions": [],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        env_file=env_file,
        env_exporter=fake_env,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    assert result["status"] == "pass"
    assert result["release_allowed"] is True
    assert result["blocking_steps"] == []
    assert result["required_actions"] == []
    assert result["required_action_groups"] == []
    assert result["steps"][-2]["key"] == "runtime_config_binding"
    assert result["steps"][-2]["status"] == "pass"
    assert result["steps"][-1]["key"] == "verification_report_hygiene"


def test_release_gate_blocks_when_env_file_is_required_but_missing(tmp_path):
    module = _load_gate_module()
    calls: list[str] = []

    def fake_scan(**_kwargs):
        calls.append("scan")
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {"status": "pass", "production_ready": True, "local_demo_ready": True}

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        require_env_file=True,
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    assert calls == ["scan", "preflight"]
    assert result["status"] == "blocked"
    assert result["gate_exit_code"] == 2
    assert result["env_validation_required"] is True
    assert result["env_validation_present"] is False
    assert result["env_file_issue_count"] == 1
    assert result["env_file_issue_codes"] == ["env_file_required"]
    assert result["blocking_steps"] == ["production_env_validation"]
    assert result["steps"][0]["status"] == "missing"
    assert result["required_actions"][0]["key"] == "env_file"
    assert result["required_actions"][0]["category"] == "configuration"
    assert result["required_actions"][0]["source_step"] == "production_env_validation"


def test_release_gate_writes_sanitized_timestamped_and_latest_reports(tmp_path):
    module = _load_gate_module()
    out_dir = tmp_path / "docs" / "verification"
    calls: list[str] = []

    def fake_scan(**_kwargs):
        calls.append("scan")
        return {
            "export_path": str(out_dir / "public-api-leak-scan-20260708T063000Z.json"),
            "latest_path": str(out_dir / "public-api-leak-scan-latest.json"),
            "status": "pass",
            "leak_count": "0",
        }

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {
            "export_path": str(out_dir / "production-preflight-20260708T063000Z.json"),
            "latest_path": str(out_dir / "production-preflight-latest.json"),
            "status": "warn",
            "production_ready": False,
            "local_demo_ready": True,
            "next_actions": [
                {
                    "key": "secrets",
                    "label": "密钥与认证",
                    "status": "warn",
                    "owner": "deployment_operator",
                    "action": (
                        "Rotate should-not:leak and "
                        + "sk-or-v1-"
                        + "should-not-be-read before release."
                    ),
                    "requires_secret_update": True,
                    "config_keys": ["APP_API_KEY", "APP_SECRET_KEY"],
                }
            ],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 6, 30, tzinfo=timezone.utc),
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    export_path = Path(result["export_path"])
    latest_path = Path(result["latest_path"])
    action_plan_path = Path(result["action_plan_path"])
    action_plan_latest_path = Path(result["action_plan_latest_path"])
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    rendered = json.dumps(exported, ensure_ascii=False)
    action_plan = action_plan_path.read_text(encoding="utf-8")
    latest_action_plan = action_plan_latest_path.read_text(encoding="utf-8")

    assert calls == ["scan", "preflight"]
    assert export_path.name == "release-gate-20260708T063000Z.json"
    assert latest_path.name == "release-gate-latest.json"
    assert action_plan_path.name == "release-gate-action-plan-20260708T063000Z.md"
    assert action_plan_latest_path.name == "release-gate-action-plan-latest.md"
    assert exported == latest
    assert action_plan == latest_action_plan
    assert exported["report_type"] == "release_gate"
    assert exported["status"] == "blocked"
    assert exported["gate_exit_code"] == 2
    assert exported["cli_exit_code"] == 2
    assert exported["run_mode"] == "strict"
    assert exported["release_allowed"] is False
    assert exported["blocking_steps"] == ["production_preflight"]
    assert exported["required_action_count"] == 1
    assert exported["required_actions"][0]["source_step"] == "production_preflight"
    assert exported["required_actions"][0]["key"] == "secrets"
    assert exported["steps"][0]["latest_path"] == "docs/verification/public-api-leak-scan-latest.json"
    assert exported["steps"][1]["latest_path"] == "docs/verification/production-preflight-latest.json"
    assert "E:\\" not in rendered
    assert "E:/" not in rendered
    assert "C:\\" not in rendered
    assert "C:/" not in rendered
    assert "127.0.0.1" not in rendered
    assert "localhost" not in rendered
    assert "sops:" not in rendered
    assert "sk-or-v1-" not in rendered
    assert "should-not:leak" not in rendered
    assert "\ufffd" not in rendered
    assert "# Release Gate Action Plan" in action_plan
    assert "Status: blocked" in action_plan
    assert "`production_preflight`" in action_plan
    assert "Secret update actions: 1" in action_plan
    assert "Secret config keys: `APP_API_KEY`, `APP_SECRET_KEY`" in action_plan
    assert "- [ ] `secrets`" in action_plan
    assert "`APP_API_KEY`" in action_plan
    assert "deployment_operator" in action_plan
    assert "- `public_api_leak_scan`: status=pass, exit_code=0" in action_plan
    assert "- `verification_report_hygiene`: status=pass, exit_code=0" in action_plan
    assert "E:\\" not in action_plan
    assert "E:/" not in action_plan
    assert "C:\\" not in action_plan
    assert "C:/" not in action_plan
    assert "127.0.0.1" not in action_plan
    assert "localhost" not in action_plan
    assert "sops:" not in action_plan
    assert "sk-or-v1-" not in action_plan
    assert "should-not:leak" not in action_plan
    assert "\ufffd" not in action_plan


def test_release_gate_preserves_materializer_command_source_label_while_sanitizing_secret_sources():
    module = _load_gate_module()

    public_result = module.public_cli_result(
        {
            "required_actions": [
                {
                    "key": "env_file",
                    "action": "Set provenance from sops:APP_API_KEY before release.",
                    "verification_commands": [
                        "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error",
                    ],
                }
            ]
        }
    )
    rendered = json.dumps(public_result, ensure_ascii=False)

    assert (
        "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error"
        in rendered
    )
    assert "sops:APP_API_KEY" not in rendered


def test_release_gate_writes_sanitized_secret_inventory_reports(tmp_path):
    module = _load_gate_module()
    out_dir = tmp_path / "docs" / "verification"

    def fake_scan(**_kwargs):
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        return {
            "status": "warn",
            "production_ready": False,
            "local_demo_ready": True,
            "next_actions": [
                {
                    "key": "secrets",
                    "label": "密钥与认证",
                    "status": "warn",
                    "category": "secrets",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["APP_API_KEY", "APP_SECRET_KEY", "OPENROUTER_API_KEY"],
                    "verification": [
                        {
                            "kind": "endpoint",
                            "target": "/api/v1/health/production-secrets-tls",
                            "expectation": "status=pass",
                        }
                    ],
                },
                {
                    "key": "database",
                    "label": "数据库",
                    "status": "warn",
                    "category": "database",
                    "owner": "database_operator",
                    "requires_secret_update": False,
                    "config_keys": ["DATABASE_URL"],
                    "uri": {"contains_credentials": True},
                    "verification": [
                        {
                            "kind": "script",
                            "target": "scripts/validate_production_env.py",
                            "expectation": "checks.DATABASE_URL.status=pass",
                        }
                    ],
                },
                {
                    "key": "object_storage",
                    "label": "对象存储",
                    "status": "warn",
                    "category": "object_storage",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_SECURE"],
                },
                {
                    "key": "SECRETS_SOURCE",
                    "label": "密钥来源证明",
                    "status": "warn",
                    "category": "secret_provenance",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["SECRETS_SOURCE"],
                    "alternative_config_keys": ["REDIS_URL_SOURCE", "RATE_LIMIT_STORAGE_URI_SOURCE"],
                    "source_provenance_target_config_keys": ["REDIS_URL", "RATE_LIMIT_STORAGE_URI"],
                },
            ],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 13, 5, tzinfo=timezone.utc),
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
    )

    inventory_path = Path(result["secret_inventory_path"])
    inventory_latest_path = Path(result["secret_inventory_latest_path"])
    inventory_markdown_path = Path(result["secret_inventory_markdown_path"])
    inventory_markdown_latest_path = Path(result["secret_inventory_markdown_latest_path"])
    action_plan = Path(result["action_plan_latest_path"]).read_text(encoding="utf-8")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    latest_inventory = json.loads(inventory_latest_path.read_text(encoding="utf-8"))
    inventory_markdown = inventory_markdown_path.read_text(encoding="utf-8")
    inventory_markdown_latest = inventory_markdown_latest_path.read_text(encoding="utf-8")
    rendered_inventory = json.dumps(inventory, ensure_ascii=False)

    assert inventory_path.name == "release-gate-secret-inventory-20260708T130500Z.json"
    assert inventory_latest_path.name == "release-gate-secret-inventory-latest.json"
    assert inventory_markdown_path.name == "release-gate-secret-inventory-20260708T130500Z.md"
    assert inventory_markdown_latest_path.name == "release-gate-secret-inventory-latest.md"
    assert inventory == latest_inventory
    assert inventory_markdown == inventory_markdown_latest
    assert result["secret_inventory_latest_path"].endswith("release-gate-secret-inventory-latest.json")
    assert result["secret_inventory_markdown_latest_path"].endswith("release-gate-secret-inventory-latest.md")
    assert inventory["report_type"] == "release_gate_secret_inventory"
    assert inventory["status"] == "required"
    assert inventory["secret_config_key_count"] == 6
    assert inventory["secret_config_keys"] == [
        "APP_API_KEY",
        "APP_SECRET_KEY",
        "OPENROUTER_API_KEY",
        "DATABASE_URL",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ]
    assert inventory["source_provenance_key"] == "SECRETS_SOURCE"
    assert inventory["source_provenance_keys"] == [
        "SECRETS_SOURCE",
        "APP_API_KEY_SOURCE",
        "APP_SECRET_KEY_SOURCE",
        "OPENROUTER_API_KEY_SOURCE",
        "DATABASE_URL_SOURCE",
        "MINIO_ACCESS_KEY_SOURCE",
        "MINIO_SECRET_KEY_SOURCE",
        "REDIS_URL_SOURCE",
        "RATE_LIMIT_STORAGE_URI_SOURCE",
    ]
    assert inventory["per_key_source_provenance_keys"] == [
        "APP_API_KEY_SOURCE",
        "APP_SECRET_KEY_SOURCE",
        "OPENROUTER_API_KEY_SOURCE",
        "DATABASE_URL_SOURCE",
        "MINIO_ACCESS_KEY_SOURCE",
        "MINIO_SECRET_KEY_SOURCE",
        "REDIS_URL_SOURCE",
        "RATE_LIMIT_STORAGE_URI_SOURCE",
    ]
    assert inventory["source_provenance_target_config_keys"] == ["REDIS_URL", "RATE_LIMIT_STORAGE_URI"]
    assert [item["key"] for item in inventory["items"]] == inventory["secret_config_keys"]
    assert inventory["items"][0]["recommended_source"] == "SOPS or deployment secret store"
    assert inventory["items"][0]["source_steps"] == ["production_preflight"]
    assert inventory["items"][0]["owners"] == ["deployment_operator"]
    assert inventory["items"][0]["categories"] == ["secrets"]
    assert inventory["items"][3]["key"] == "DATABASE_URL"
    assert inventory["items"][3]["classification"] == "credential_bearing_uri"
    assert inventory["items"][3]["owners"] == ["database_operator"]
    assert "MINIO_SECURE" not in inventory["secret_config_keys"]
    assert "Secret inventory: docs/verification/release-gate-secret-inventory-latest.md" in action_plan
    assert "# Release Gate Secret Inventory" in inventory_markdown
    assert "- [ ] `APP_API_KEY`" in inventory_markdown
    assert "- [ ] `DATABASE_URL`" in inventory_markdown
    assert "SOPS or deployment secret store" in inventory_markdown
    assert "## Source Provenance Runbook" in inventory_markdown
    assert "docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md" in inventory_markdown
    assert "Global source marker: `SECRETS_SOURCE`" in inventory_markdown
    assert "Per-key source markers: `APP_API_KEY_SOURCE`, `APP_SECRET_KEY_SOURCE`" in inventory_markdown
    assert (
        "Source provenance target config keys: `REDIS_URL`, `RATE_LIMIT_STORAGE_URI`"
        in inventory_markdown
    )
    assert "python scripts/validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --fail-on-error" in inventory_markdown
    assert "python scripts/validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --env-file <candidate-production-env> --fail-on-error" in inventory_markdown
    assert '"value"' not in rendered_inventory.lower()
    assert "secret_value" not in rendered_inventory.lower()
    assert "E:\\" not in rendered_inventory
    assert "E:/" not in rendered_inventory
    assert "C:\\" not in rendered_inventory
    assert "C:/" not in rendered_inventory
    assert "127.0.0.1" not in rendered_inventory
    assert "localhost" not in rendered_inventory
    assert "sk-or-v1-" not in rendered_inventory


def test_release_gate_runs_secret_inventory_validation_as_gate_step(tmp_path):
    module = _load_gate_module()
    out_dir = tmp_path / "docs" / "verification"
    calls: list[str] = []

    def fake_scan(**_kwargs):
        calls.append("scan")
        return {"status": "pass", "leak_count": "0"}

    def fake_preflight(**_kwargs):
        calls.append("preflight")
        return {
            "status": "warn",
            "production_ready": False,
            "local_demo_ready": True,
            "next_actions": [
                {
                    "key": "secrets",
                    "label": "密钥与认证",
                    "status": "warn",
                    "category": "secrets",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["APP_API_KEY", "APP_SECRET_KEY"],
                }
            ],
        }

    def fake_secret_validator(inventory_path, **kwargs):
        calls.append(f"secret:{Path(inventory_path).name}")
        assert Path(inventory_path).name == "release-gate-secret-inventory-20260708T131000Z.json"
        assert kwargs["env_file"] is None
        return {
            "report_type": "secret_inventory_validation",
            "status": "fail",
            "summary": {"pass": 0, "fail": 2, "total": 2},
            "export_path": str(out_dir / "secret-inventory-validation-20260708T131000Z.json"),
            "latest_path": str(out_dir / "secret-inventory-validation-latest.json"),
            "next_actions": [
                {
                    "key": "APP_API_KEY",
                    "label": "APP_API_KEY",
                    "status": "fail",
                    "category": "secret_inventory_validation",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["APP_API_KEY"],
                }
            ],
        }

    result = module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=out_dir,
        project_root=tmp_path,
        now=datetime(2026, 7, 8, 13, 10, tzinfo=timezone.utc),
        scan_exporter=fake_scan,
        preflight_exporter=fake_preflight,
        secret_inventory_validator=fake_secret_validator,
    )
    action_plan = Path(result["action_plan_latest_path"]).read_text(encoding="utf-8")
    latest = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))

    assert calls == ["scan", "preflight", "secret:release-gate-secret-inventory-20260708T131000Z.json"]
    assert result["status"] == "blocked"
    assert result["blocking_steps"] == ["production_preflight", "secret_inventory_validation"]
    assert result["secret_inventory_validation_latest_path"].endswith("secret-inventory-validation-latest.json")
    assert [step["key"] for step in result["steps"]] == [
        "public_api_leak_scan",
        "production_preflight",
        "secret_inventory_validation",
        "verification_report_hygiene",
    ]
    assert result["steps"][2]["status"] == "fail"
    assert result["steps"][2]["exit_code"] == 2
    assert result["required_actions"][-1]["source_step"] == "secret_inventory_validation"
    assert result["required_actions"][-1]["key"] == "APP_API_KEY"
    assert latest["secret_inventory_validation_latest_path"] == "docs/verification/secret-inventory-validation-latest.json"
    assert "Secret inventory validation: docs/verification/secret-inventory-validation-latest.json" in action_plan
    assert "Secret inventory validation summary: pass=0, fail=2, total=2" in action_plan
    assert "- `secret_inventory_validation`: status=fail, exit_code=2" in action_plan
