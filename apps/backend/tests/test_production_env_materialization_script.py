from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "materialize_production_env.py"
    spec = importlib.util.spec_from_file_location("materialize_production_env", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _complete_env() -> dict[str, str]:
    return {
        "DEPLOYMENT_CONFIG_ID": "prod-20260709T090000Z",
        "APP_API_KEY": "app-api-key-that-is-long-enough-for-prod",
        "APP_SECRET_KEY": "prod-secret-key-that-is-longer-than-32-characters",
        "OPENROUTER_KEY": "sk-or-v1-" + "prodvalidtokenlongenough",
        "POSTGRES_PASSWORD": "postgres-password-for-prod",
        "MINIO_ROOT_USER": "prod-minio-root-user",
        "MINIO_ROOT_PASSWORD": "prod-minio-root-password",
        "SENTRY_DSN": "https://public" + "@example.ingest.sentry.io/123",
    }


def _complete_project_sops_short_env() -> dict[str, str]:
    return {
        "adms_deployment_config_id": "prod-20260710T110000Z",
        "adms_app_api_key": "app-api-key-from-adms-sops-short-name",
        "adms_app_secret_key": "prod-secret-from-adms-sops-short-name-32chars",
        "openrouter_key": "sk-or-v1-" + "prodvalidtokenlongenough",
        "adms_postgres_password": "postgres-password-from-adms-sops",
        "adms_minio_root_user": "adms-prod-minio-root-user",
        "adms_minio_root_password": "adms-prod-minio-root-password",
        "adms_sentry_dsn": "https://public" + "@example.ingest.sentry.io/456",
    }


def test_materialize_production_env_reports_missing_values_without_writing(tmp_path):
    module = _load_module()
    output = tmp_path / "apps" / "backend" / ".env.prod"

    report = module.materialize_production_env(
        env_values={"APP_API_KEY": "app-api-key-that-is-long-enough-for-prod"},
        output=output,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        now=datetime(2026, 7, 9, 9, 0, tzinfo=timezone.utc),
        source_label="sops:production",
    )

    latest = tmp_path / "docs" / "verification" / "production-env-materialization-latest.json"
    exported = json.loads(latest.read_text(encoding="utf-8"))

    assert output.exists() is False
    assert exported["status"] == "fail"
    assert exported["output_written"] is False
    assert exported["output_path"] == "apps/backend/.env.prod"
    assert "APP_SECRET_KEY" in exported["missing_required_keys"]
    assert "OPENROUTER_API_KEY" in exported["missing_required_keys"]
    assert exported["next_actions"][0]["key"] == "missing_required_config"


def test_materialize_production_env_emits_structured_missing_config_requests(tmp_path):
    module = _load_module()
    output = tmp_path / "apps" / "backend" / ".env.prod"

    report = module.materialize_production_env(
        env_values={
            "APP_API_KEY": "app-api-key-that-is-long-enough-for-prod",
            "OPENROUTER_API_KEY": "sk-or-v1-" + "prodvalidtokenlongenough",
        },
        output=output,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        now=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        source_label="sops:production",
        dry_run=True,
    )

    exported = json.loads(
        (tmp_path / "docs" / "verification" / "production-env-materialization-latest.json").read_text(
            encoding="utf-8"
        )
    )
    rendered = json.dumps(exported, ensure_ascii=False)
    requests = {item["config_key"]: item for item in exported["missing_config_requests"]}

    assert report["status"] == "fail"
    assert exported["missing_config_request_count"] == len(exported["missing_required_keys"])
    assert requests["APP_SECRET_KEY"]["suggested_sops_key"] == "adms_app_secret_key"
    assert requests["APP_SECRET_KEY"]["sensitivity"] == "secret"
    assert requests["APP_SECRET_KEY"]["source_marker_key"] == "APP_SECRET_KEY_SOURCE"
    assert requests["DEPLOYMENT_CONFIG_ID"]["sensitivity"] == "non_secret_config"
    assert requests["DATABASE_URL"]["can_be_derived_from"] == ["POSTGRES_PASSWORD"]
    assert requests["DATABASE_URL"]["suggested_sops_key"] == "adms_database_url"
    assert requests["MINIO_ROOT_PASSWORD"]["suggested_sops_key"] == "adms_minio_root_password"
    assert all(item["accepted_source_prefixes"] for item in requests.values())
    assert "prodvalidtokenlongenough" not in rendered


def test_materialize_production_env_writes_ignored_candidate_and_sanitized_report(tmp_path):
    module = _load_module()
    output = tmp_path / "apps" / "backend" / ".env.prod"

    report = module.materialize_production_env(
        env_values=_complete_env(),
        output=output,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        now=datetime(2026, 7, 9, 9, 5, tzinfo=timezone.utc),
        source_label="sops:production",
    )

    latest = tmp_path / "docs" / "verification" / "production-env-materialization-latest.json"
    exported = json.loads(latest.read_text(encoding="utf-8"))
    rendered = json.dumps(exported, ensure_ascii=False)
    env_text = output.read_text(encoding="utf-8")

    assert report["status"] == "pass"
    assert exported["status"] == "pass"
    assert exported["output_written"] is True
    assert exported["output_path"] == "apps/backend/.env.prod"
    assert exported["written_key_count"] > 20
    assert "OPENROUTER_API_KEY=" in env_text
    assert "OPENROUTER_KEY=" not in env_text
    assert "DATABASE_URL=postgresql://agent_user:postgres-password-for-prod@postgres:5432/agent_db" in env_text
    assert "MINIO_ACCESS_KEY=prod-minio-root-user" in env_text
    assert "MINIO_SECRET_KEY=prod-minio-root-password" in env_text
    assert "SECRETS_SOURCE=sops:production" in env_text
    assert exported["alias_resolution"]["OPENROUTER_API_KEY"] == "legacy_env_alias"
    assert "OPENROUTER_KEY" not in rendered
    assert "prodvalidtokenlongenough" not in rendered
    assert "postgres-password-for-prod" not in rendered
    assert "prod-minio-root-password" not in rendered
    assert str(tmp_path) not in rendered


def test_materialize_production_env_accepts_sops_short_openrouter_key(tmp_path):
    module = _load_module()
    output = tmp_path / "apps" / "backend" / ".env.prod"
    env_values = _complete_env()
    openrouter_value = env_values.pop("OPENROUTER_KEY")
    env_values["openrouter_key"] = openrouter_value

    report = module.materialize_production_env(
        env_values=env_values,
        output=output,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        now=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
        source_label="sops:production",
    )

    exported = json.loads(
        (tmp_path / "docs" / "verification" / "production-env-materialization-latest.json").read_text(
            encoding="utf-8"
        )
    )
    rendered = json.dumps(exported, ensure_ascii=False)
    env_text = output.read_text(encoding="utf-8")

    assert report["status"] == "pass"
    assert "OPENROUTER_API_KEY=sk-or-v1-prodvalidtokenlongenough" in env_text
    assert exported["alias_resolution"]["OPENROUTER_API_KEY"] == "sops_short_name"
    assert "prodvalidtokenlongenough" not in rendered


def test_materialize_production_env_accepts_project_sops_short_names_from_request_list(tmp_path):
    module = _load_module()
    output = tmp_path / "apps" / "backend" / ".env.prod"

    report = module.materialize_production_env(
        env_values=_complete_project_sops_short_env(),
        output=output,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        now=datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc),
        source_label="sops:production",
    )

    exported = json.loads(
        (tmp_path / "docs" / "verification" / "production-env-materialization-latest.json").read_text(
            encoding="utf-8"
        )
    )
    rendered = json.dumps(exported, ensure_ascii=False)
    env_text = output.read_text(encoding="utf-8")

    assert report["status"] == "pass"
    assert exported["missing_required_keys"] == []
    assert exported["missing_config_requests"] == []
    assert "APP_API_KEY=app-api-key-from-adms-sops-short-name" in env_text
    assert "APP_SECRET_KEY=prod-secret-from-adms-sops-short-name-32chars" in env_text
    assert "DEPLOYMENT_CONFIG_ID=prod-20260710T110000Z" in env_text
    assert "POSTGRES_PASSWORD=postgres-password-from-adms-sops" in env_text
    assert "DATABASE_URL=postgresql://agent_user:postgres-password-from-adms-sops@postgres:5432/agent_db" in env_text
    assert "MINIO_ROOT_USER=adms-prod-minio-root-user" in env_text
    assert "MINIO_ROOT_PASSWORD=adms-prod-minio-root-password" in env_text
    assert "SENTRY_DSN=https://public@example.ingest.sentry.io/456" in env_text
    assert exported["alias_resolution"]["APP_API_KEY"] == "project_sops_short_name"
    assert exported["alias_resolution"]["DATABASE_URL"] == "derived_from_project_sops_short_name"
    assert "app-api-key-from-adms-sops-short-name" not in rendered
    assert "postgres-password-from-adms-sops" not in rendered


def test_materialize_production_env_dry_run_validates_without_writing_secret_file(tmp_path):
    module = _load_module()
    output = tmp_path / "apps" / "backend" / ".env.prod"

    report = module.materialize_production_env(
        env_values=_complete_env(),
        output=output,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        now=datetime(2026, 7, 10, 9, 10, tzinfo=timezone.utc),
        source_label="sops:production",
        dry_run=True,
    )

    exported = json.loads(
        (tmp_path / "docs" / "verification" / "production-env-materialization-latest.json").read_text(
            encoding="utf-8"
        )
    )

    assert report["status"] == "pass"
    assert report["dry_run"] is True
    assert report["would_write_output"] is True
    assert report["output_written"] is False
    assert output.exists() is False
    assert exported["status"] == "pass"
    assert exported["dry_run"] is True
    assert exported["would_write_output"] is True


def test_materialize_production_env_refuses_to_overwrite_without_flag(tmp_path):
    module = _load_module()
    output = tmp_path / "apps" / "backend" / ".env.prod"
    output.parent.mkdir(parents=True)
    output.write_text("EXISTING=true\n", encoding="utf-8")

    report = module.materialize_production_env(
        env_values=_complete_env(),
        output=output,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
        now=datetime(2026, 7, 9, 9, 10, tzinfo=timezone.utc),
        source_label="sops:production",
    )

    exported = json.loads(
        (tmp_path / "docs" / "verification" / "production-env-materialization-latest.json").read_text(
            encoding="utf-8"
        )
    )

    assert report["status"] == "blocked"
    assert exported["status"] == "blocked"
    assert exported["output_written"] is False
    assert exported["next_actions"][0]["key"] == "output_exists"
    assert output.read_text(encoding="utf-8") == "EXISTING=true\n"
