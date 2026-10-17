from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "validate_production_env.py"
    spec = importlib.util.spec_from_file_location("validate_production_env", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_openrouter_key() -> str:
    return "sk-or-v1-" + "prodvalidtokenlongenough"


def _test_openrouter_env_line() -> str:
    return "OPENROUTER_API_KEY=" + _test_openrouter_key()


def _test_unread_openrouter_key() -> str:
    return "sk-or-v1-" + "should-not-be-read"


def _test_database_url(password: str = "db-secret") -> str:
    return "postgresql://agent_user" + f":{password}" + "@postgres:5432/agent_db"


def _test_database_env_line(password: str = "db-secret") -> str:
    return "DATABASE_URL=" + _test_database_url(password)


def _test_process_database_url() -> str:
    return "postgresql://should-not" + ":leak" + "@postgres:5432/prod"


def _test_sentry_dsn() -> str:
    return "https://public" + "@example.ingest.sentry.io/123"


def _test_sentry_env_line() -> str:
    return "SENTRY_DSN=" + _test_sentry_dsn()


def _test_app_secret_env_line(value: str = "prod-secret-key-that-is-longer-than-32-characters") -> str:
    return "APP_SECRET_KEY=" + value


def _valid_production_env_text(deployment_config_id: str = "prod-20260709T020000Z") -> str:
    return (
        "\n".join(
            [
                "APP_ENV=production",
                f"DEPLOYMENT_CONFIG_ID={deployment_config_id}",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n"
    )


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.local"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Production Env Tests"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_validate_production_env_rejects_placeholders_and_default_values(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=<REQUIRED_RELEASE_CONFIG_ID>",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=<REQUIRED_CONTROLLED_SECRET_SOURCE>",
                "DATABASE_URL=sqlite:///./dev.db",
                "APP_API_KEY=请替换为强随机字符串",
                _test_app_secret_env_line("dev-secret-key"),
                "OPENROUTER_API_KEY=请替换为真实 OpenRouter key",
                "REDIS_ENABLED=false",
                "RATE_LIMIT_STORAGE_URI=memory://",
                "MINIO_ENDPOINT=localhost:9000",
                "MINIO_SECURE=false",
                "MINIO_ACCESS_KEY=minioadmin",
                "MINIO_SECRET_KEY=minioadmin",
                "MINIO_BUCKET=agent-files",
                "LOG_FORMAT=pretty",
                "SENTRY_DSN=",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 10, tzinfo=timezone.utc),
    )
    rendered = json.dumps(result, ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert result["next_action_count"] >= 7
    assert "APP_API_KEY" in [item["key"] for item in result["next_actions"]]
    assert "DEPLOYMENT_CONFIG_ID" in [item["key"] for item in result["next_actions"]]
    assert action_map["APP_API_KEY"]["category"] == "secrets"
    assert action_map["DATABASE_URL"]["uri"]["contains_credentials"] is False
    assert action_map["DATABASE_URL"]["uri"]["scheme"] == "sqlite"
    assert result["placeholder_count"] >= 2
    assert "DATABASE_URL must not use SQLite in production" in result["production_config_errors"]
    assert check_map["APP_API_KEY"]["status"] == "fail"
    assert check_map["APP_API_KEY"]["placeholder_like"] is True
    assert check_map["APP_SECRET_KEY"]["default_like"] is True
    assert check_map["DATABASE_URL"]["status"] == "fail"
    assert check_map["SECRETS_SOURCE"]["status"] == "fail"
    assert check_map["SECRETS_SOURCE"]["source_provenance_ok"] is False
    assert check_map["RATE_LIMIT_STORAGE_URI"]["status"] == "fail"
    assert check_map["LOG_FORMAT"]["status"] == "fail"
    assert check_map["SENTRY_DSN"]["status"] == "fail"
    assert "dev-secret-key" not in rendered
    assert "minioadmin" not in rendered
    assert "请替换" not in rendered


def test_validate_production_env_accepts_safe_shape_without_leaking_values(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 11, tzinfo=timezone.utc),
    )
    rendered = json.dumps(result, ensure_ascii=False)
    export_path = Path(result["export_path"])
    latest_path = Path(result["latest_path"])

    assert result["status"] == "pass"
    assert result["release_allowed"] is True
    assert result["deployment_config_id"] == "prod-20260708T081500Z"
    assert result["production_config_errors"] == []
    check_map = {item["key"]: item for item in result["checks"]}
    assert check_map["DATABASE_URL"]["uri"]["scheme"] == "postgresql"
    assert check_map["DATABASE_URL"]["uri"]["contains_credentials"] is True
    assert check_map["SECRETS_SOURCE"]["status"] == "pass"
    assert check_map["SECRETS_SOURCE"]["source_marker_family"] == "sops"
    assert check_map["SECRETS_SOURCE"]["source_provenance_ok"] is True
    assert check_map["REDIS_URL"]["uri"]["scheme"] == "redis"
    assert check_map["REDIS_URL"]["uri"]["contains_credentials"] is False
    assert export_path.name == "production-env-validation-20260708T071100Z.json"
    assert latest_path.name == "production-env-validation-latest.json"
    assert json.loads(export_path.read_text(encoding="utf-8")) == json.loads(
        latest_path.read_text(encoding="utf-8")
    )
    assert "db-secret" not in rendered
    assert "agent_user" not in rendered
    assert "app-api-key-that-is-long-enough-for-prod" not in rendered
    assert _test_openrouter_key() not in rendered
    assert "prod-secret-key" not in rendered


def test_validate_production_env_rejects_tracked_private_env_file_without_leaking_values(tmp_path):
    module = _load_module()
    _init_git_repo(tmp_path)
    env_path = tmp_path / "ops" / "prod.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        _valid_production_env_text("prod-20260709T020100Z"),
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(["git", "add", "ops/prod.env"], cwd=tmp_path, check=True, capture_output=True, text=True)

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 2, 1, tzinfo=timezone.utc),
    )
    rendered = json.dumps(result, ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert "env_file_tracked_by_git" in result["env_file_issue_codes"]
    assert check_map["env_file_git_safety"]["status"] == "fail"
    assert check_map["env_file_git_safety"]["git_tracked"] is True
    assert check_map["env_file_git_safety"]["git_ignored"] is False
    assert action_map["env_file_git_safety"]["requires_secret_update"] is False
    assert action_map["env_file_git_safety"]["config_keys"] == []
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_accepts_gitignored_private_env_file(tmp_path):
    module = _load_module()
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(
        ".env\n.env.*\n!.env.example\n!.env.prod.example\n",
        encoding="utf-8",
        newline="\n",
    )
    env_path = tmp_path / "apps" / "backend" / ".env.prod"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        _valid_production_env_text("prod-20260709T020200Z"),
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 2, 2, tzinfo=timezone.utc),
    )
    check_map = {item["key"]: item for item in result["checks"]}

    assert result["status"] == "pass"
    assert result["release_allowed"] is True
    assert result["env_file_issue_codes"] == []
    assert check_map["env_file_git_safety"]["status"] == "pass"
    assert check_map["env_file_git_safety"]["inside_project"] is True
    assert check_map["env_file_git_safety"]["template_file"] is False
    assert check_map["env_file_git_safety"]["git_tracked"] is False
    assert check_map["env_file_git_safety"]["git_ignored"] is True


def test_validate_production_env_accepts_compose_deployment_values_without_leaking(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    postgres_password = "pgcred-controlled-random-token-20260709"
    minio_root_user = "minio-root-user-from-controlled-source"
    minio_root_password = "minio-root-credential-token-20260709"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260709T003500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                f"POSTGRES_PASSWORD={postgres_password}",
                f"MINIO_ROOT_USER={minio_root_user}",
                f"MINIO_ROOT_PASSWORD={minio_root_password}",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 0, 35, tzinfo=timezone.utc),
    )
    rendered = json.dumps(result, ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}

    assert result["status"] == "pass"
    assert result["release_allowed"] is True
    assert "env_file_unknown_keys" not in check_map
    assert check_map["POSTGRES_PASSWORD"]["status"] == "pass"
    assert check_map["POSTGRES_PASSWORD"]["category"] == "compose_deployment_secret"
    assert check_map["MINIO_ROOT_USER"]["status"] == "pass"
    assert check_map["MINIO_ROOT_PASSWORD"]["status"] == "pass"
    assert postgres_password not in rendered
    assert minio_root_user not in rendered
    assert minio_root_password not in rendered


def test_validate_production_env_marks_credential_uri_actions_as_secret_updates(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line("<REQUIRED_DB_PASSWORD>"),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 14, tzinfo=timezone.utc),
    )
    rendered = json.dumps(result, ensure_ascii=False)
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert action_map["DATABASE_URL"]["requires_secret_update"] is True
    assert action_map["DATABASE_URL"]["uri"]["contains_credentials"] is True
    assert action_map["DATABASE_URL"]["uri"]["scheme"] == "postgresql"
    assert "agent_user" not in rendered
    assert "REQUIRED_DB_PASSWORD" not in rendered


def test_validate_production_env_marks_controlled_dsn_placeholder_as_secret_update(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260709T012900Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                "DATABASE_URL=<POSTGRES_DSN_FROM_CONTROLLED_SECRET_SOURCE>",
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 1, 29, tzinfo=timezone.utc),
    )
    rendered = json.dumps(result, ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert check_map["DATABASE_URL"]["placeholder_like"] is True
    assert check_map["DATABASE_URL"]["uri"]["contains_credentials"] is False
    assert action_map["DATABASE_URL"]["requires_secret_update"] is True
    assert action_map["DATABASE_URL"]["config_keys"] == ["DATABASE_URL"]
    assert "POSTGRES_DSN_FROM_CONTROLLED_SECRET_SOURCE" not in rendered


def test_validate_production_env_cli_json_is_ascii_safe():
    module = _load_module()

    rendered = module.format_cli_json({"label": "生产配置", "status": "warn"})

    assert "生产配置" not in rendered
    assert "\\u751f\\u4ea7\\u914d\\u7f6e" in rendered
    assert json.loads(rendered)["label"] == "生产配置"


def test_settings_snapshot_ignores_process_environment(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("DATABASE_URL", _test_process_database_url())
    monkeypatch.setenv("OPENROUTER_API_KEY", _test_unread_openrouter_key())

    cfg = module._settings_from_values({"APP_ENV": "production", "APP_DEBUG": "false"})

    assert cfg.DATABASE_URL == "sqlite:///./dev.db"
    assert cfg.OPENROUTER_API_KEY == ""


def test_validate_production_env_missing_env_file_writes_actionable_report(tmp_path):
    module = _load_module()
    env_path = tmp_path / "missing" / ".env.production"
    out_dir = tmp_path / "docs" / "verification"

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=out_dir,
        now=datetime(2026, 7, 8, 7, 12, tzinfo=timezone.utc),
    )
    public = module.public_cli_result(result, project_root=tmp_path)
    exported = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))
    rendered = json.dumps(public, ensure_ascii=False)

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert result["summary"] == {"pass": 0, "fail": 1, "total": 1}
    assert result["env_file_issue_count"] == 1
    assert result["env_file_issue_codes"] == ["env_file_missing"]
    assert result["next_action_count"] == 1
    assert result["next_actions"][0]["key"] == "env_file"
    assert result["next_actions"][0]["category"] == "configuration"
    assert result["next_actions"][0]["action"] == (
        "Materialize the approved apps/backend/.env.prod candidate from SOPS or the deployment "
        "secret store, validate it, then rerun production env validation."
    )
    assert (
        "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error"
        in result["next_actions"][0]["verification_commands"]
    )
    assert (
        "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error"
        in result["next_actions"][0]["verification_commands"]
    )
    assert result["checks"][0]["key"] == "env_file"
    assert result["checks"][0]["status"] == "fail"
    assert Path(result["export_path"]).name == "production-env-validation-20260708T071200Z.json"
    assert Path(result["latest_path"]).name == "production-env-validation-latest.json"
    assert exported["status"] == "fail"
    assert exported["next_actions"][0]["key"] == "env_file"
    assert exported["next_actions"][0]["action"] == result["next_actions"][0]["action"]
    assert str(tmp_path) not in rendered
    assert "FileNotFoundError" not in rendered


def test_validate_production_env_invalid_encoding_writes_actionable_report(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    out_dir = tmp_path / "docs" / "verification"
    env_path.write_bytes(b"\xff\xfe\xfd\n")

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=out_dir,
        now=datetime(2026, 7, 8, 7, 13, tzinfo=timezone.utc),
    )
    public = module.public_cli_result(result, project_root=tmp_path)
    exported = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))
    rendered = json.dumps(public, ensure_ascii=False)

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert result["env_file_issue_count"] == 1
    assert result["env_file_issue_codes"] == ["env_file_read_error"]
    assert result["checks"][0]["key"] == "env_file"
    assert result["checks"][0]["issue_code"] == "env_file_read_error"
    assert result["checks"][0]["error_type"] == "UnicodeDecodeError"
    assert result["next_actions"][0]["key"] == "env_file"
    assert result["production_config_errors"] == ["Production env file could not be read as UTF-8 text."]
    assert Path(result["export_path"]).name == "production-env-validation-20260708T071300Z.json"
    assert exported["checks"][0]["issue_code"] == "env_file_read_error"
    assert str(tmp_path) not in rendered
    assert "UnicodeDecodeError" in rendered
    assert "invalid start byte" not in rendered


def test_validate_production_env_rejects_malformed_lines_without_leaking_content(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    out_dir = tmp_path / "docs" / "verification"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "BROKEN_SECRET_LINE_SHOULD_NOT_LEAK",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=out_dir,
        now=datetime(2026, 7, 8, 7, 14, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    exported = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))
    check_map = {item["key"]: item for item in result["checks"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert result["env_file_issue_count"] == 1
    assert result["env_file_issue_codes"] == ["env_file_syntax_error"]
    assert result["next_action_count"] == 1
    assert result["next_actions"][0]["key"] == "env_file_syntax"
    assert result["production_config_errors"] == ["Production env file contains malformed non-comment lines."]
    assert check_map["env_file_syntax"]["status"] == "fail"
    assert check_map["env_file_syntax"]["issue_code"] == "env_file_syntax_error"
    assert check_map["env_file_syntax"]["line_numbers"] == [2]
    assert exported["checks"][-1]["key"] == "env_file_syntax"
    assert "BROKEN_SECRET_LINE_SHOULD_NOT_LEAK" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_rejects_duplicate_keys_without_leaking_values(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    out_dir = tmp_path / "docs" / "verification"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                "APP_API_KEY=second-app-api-key-that-must-not-leak",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=out_dir,
        now=datetime(2026, 7, 8, 7, 15, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    exported = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))
    check_map = {item["key"]: item for item in result["checks"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert result["env_file_issue_count"] == 1
    assert result["env_file_issue_codes"] == ["env_file_duplicate_key"]
    assert result["next_action_count"] == 1
    assert result["next_actions"][0]["key"] == "env_file_duplicate_keys"
    assert result["production_config_errors"] == ["Production env file contains duplicate keys."]
    assert check_map["env_file_duplicate_keys"]["status"] == "fail"
    assert check_map["env_file_duplicate_keys"]["issue_code"] == "env_file_duplicate_key"
    assert check_map["env_file_duplicate_keys"]["duplicate_keys"] == ["APP_API_KEY"]
    assert check_map["env_file_duplicate_keys"]["line_numbers"] == [6, 7]
    assert exported["checks"][-1]["key"] == "env_file_duplicate_keys"
    assert "app-api-key-that-is-long-enough-for-prod" not in rendered
    assert "second-app-api-key-that-must-not-leak" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_rejects_unknown_keys_without_leaking_values(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    out_dir = tmp_path / "docs" / "verification"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "# typo below should report the physical line number",
                "OPENROUTER_APIKEY=typo-secret-value-that-must-not-leak",
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=out_dir,
        now=datetime(2026, 7, 8, 7, 16, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    exported = json.loads(Path(result["latest_path"]).read_text(encoding="utf-8"))
    check_map = {item["key"]: item for item in result["checks"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert result["env_file_issue_count"] == 1
    assert result["env_file_issue_codes"] == ["env_file_unknown_key"]
    assert result["next_action_count"] == 1
    assert result["next_actions"][0]["key"] == "env_file_unknown_keys"
    assert result["next_actions"][0]["config_keys"] == ["OPENROUTER_APIKEY"]
    assert result["production_config_errors"] == ["Production env file contains unknown keys."]
    assert check_map["env_file_unknown_keys"]["status"] == "fail"
    assert check_map["env_file_unknown_keys"]["issue_code"] == "env_file_unknown_key"
    assert check_map["env_file_unknown_keys"]["unknown_keys"] == ["OPENROUTER_APIKEY"]
    assert check_map["env_file_unknown_keys"]["line_numbers"] == [10]
    assert exported["checks"][-1]["key"] == "env_file_unknown_keys"
    assert "typo-secret-value-that-must-not-leak" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_requires_secret_source_provenance_metadata(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 17, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert check_map["SECRETS_SOURCE"]["status"] == "fail"
    assert check_map["SECRETS_SOURCE"]["source_marker_present"] is False
    assert check_map["SECRETS_SOURCE"]["source_provenance_ok"] is False
    assert check_map["SECRETS_SOURCE"]["source_provenance_mode"] == "global_or_per_key"
    assert check_map["SECRETS_SOURCE"]["alternative_config_keys"] == [
        f"{key}_SOURCE" for key in module.REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS
    ]
    assert check_map["SECRETS_SOURCE"]["source_provenance_target_config_keys"] == list(
        module.REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS
    )
    assert action_map["SECRETS_SOURCE"]["category"] == "secret_provenance"
    assert action_map["SECRETS_SOURCE"]["requires_secret_update"] is True
    assert action_map["SECRETS_SOURCE"]["config_keys"] == ["SECRETS_SOURCE"]
    assert action_map["SECRETS_SOURCE"]["alternative_config_keys"] == check_map["SECRETS_SOURCE"][
        "alternative_config_keys"
    ]
    assert action_map["SECRETS_SOURCE"]["source_provenance_target_config_keys"] == check_map[
        "SECRETS_SOURCE"
    ]["source_provenance_target_config_keys"]
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_accepts_per_key_source_metadata_without_global_source(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                _test_database_env_line(),
                "DATABASE_URL_SOURCE=vault:DATABASE_URL",
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                "APP_API_KEY_SOURCE=sops:APP_API_KEY",
                _test_app_secret_env_line(),
                "APP_SECRET_KEY_SOURCE=sops:APP_SECRET_KEY",
                _test_openrouter_env_line(),
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
                _test_sentry_env_line(),
                "SENTRY_DSN_SOURCE=sops:SENTRY_DSN",
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 18, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}

    assert result["status"] == "pass"
    assert result["release_allowed"] is True
    assert check_map["APP_API_KEY_SOURCE"]["status"] == "pass"
    assert check_map["APP_API_KEY_SOURCE"]["source_marker_family"] == "sops"
    assert check_map["DATABASE_URL_SOURCE"]["source_marker_family"] == "vault"
    assert "sops:APP_API_KEY" not in rendered
    assert "vault:DATABASE_URL" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_requires_source_for_enabled_optional_sensitive_keys(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                _test_database_env_line(),
                "DATABASE_URL_SOURCE=vault:DATABASE_URL",
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                "APP_API_KEY_SOURCE=sops:APP_API_KEY",
                _test_app_secret_env_line(),
                "APP_SECRET_KEY_SOURCE=sops:APP_SECRET_KEY",
                _test_openrouter_env_line(),
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
                _test_sentry_env_line(),
                "SENTRY_DSN_SOURCE=sops:SENTRY_DSN",
                "COINGECKO_API_KEY=coingecko-key-that-must-not-leak",
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 20, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert check_map["SECRETS_SOURCE"]["status"] == "fail"
    assert check_map["SECRETS_SOURCE"]["alternative_config_keys"] == [
        f"{key}_SOURCE"
        for key in [
            *module.REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS,
            "COINGECKO_API_KEY",
        ]
    ]
    assert check_map["SECRETS_SOURCE"]["source_provenance_target_config_keys"] == [
        *module.REQUIRED_SOURCE_PROVENANCE_CONFIG_KEYS,
        "COINGECKO_API_KEY",
    ]
    assert action_map["SECRETS_SOURCE"]["source_provenance_target_config_keys"] == check_map[
        "SECRETS_SOURCE"
    ]["source_provenance_target_config_keys"]
    assert "FRED_API_KEY_SOURCE" not in action_map["SECRETS_SOURCE"]["alternative_config_keys"]
    assert "coingecko-key-that-must-not-leak" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_rejects_placeholder_optional_sensitive_values(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                "COINGECKO_API_KEY=<REQUIRED_COINGECKO_API_KEY>",
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 22, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert check_map["COINGECKO_API_KEY"]["status"] == "fail"
    assert check_map["COINGECKO_API_KEY"]["placeholder_like"] is True
    assert check_map["COINGECKO_API_KEY"]["issue_code"] == "invalid_optional_sensitive_value"
    assert action_map["COINGECKO_API_KEY"]["requires_secret_update"] is True
    assert action_map["COINGECKO_API_KEY"]["config_keys"] == ["COINGECKO_API_KEY"]
    assert "REQUIRED_COINGECKO_API_KEY" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_rejects_stale_optional_source_metadata(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                _test_database_env_line(),
                "DATABASE_URL_SOURCE=vault:DATABASE_URL",
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                "APP_API_KEY_SOURCE=sops:APP_API_KEY",
                _test_app_secret_env_line(),
                "APP_SECRET_KEY_SOURCE=sops:APP_SECRET_KEY",
                _test_openrouter_env_line(),
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
                _test_sentry_env_line(),
                "SENTRY_DSN_SOURCE=sops:SENTRY_DSN",
                "FRED_API_KEY_SOURCE=sops:FRED_API_KEY",
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 21, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert check_map["FRED_API_KEY_SOURCE"]["status"] == "fail"
    assert check_map["FRED_API_KEY_SOURCE"]["issue_code"] == "stale_optional_source_metadata"
    assert check_map["FRED_API_KEY_SOURCE"]["target_config_key"] == "FRED_API_KEY"
    assert action_map["FRED_API_KEY_SOURCE"]["config_keys"] == ["FRED_API_KEY_SOURCE"]
    assert action_map["FRED_API_KEY_SOURCE"]["alternative_config_keys"] == ["FRED_API_KEY"]
    assert "sops:FRED_API_KEY" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered


def test_validate_production_env_rejects_untrusted_per_key_source_metadata(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env.production"
    env_path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DEPLOYMENT_CONFIG_ID=prod-20260708T081500Z",
                "APP_DEBUG=false",
                "SECRETS_SOURCE=sops:production",
                _test_database_env_line(),
                "APP_API_KEY=app-api-key-that-is-long-enough-for-prod",
                "APP_API_KEY_SOURCE=plain-env:manual-copy",
                _test_app_secret_env_line(),
                _test_openrouter_env_line(),
                "OPENROUTER_MOCK=false",
                "REDIS_ENABLED=true",
                "REDIS_URL=redis://redis:6379/0",
                "RATE_LIMIT_STORAGE_URI=redis://redis:6379/1",
                "MINIO_ENDPOINT=minio-prod.internal:9000",
                "MINIO_SECURE=true",
                "MINIO_ACCESS_KEY=prod-access-key",
                "MINIO_SECRET_KEY=prod-secret-key",
                "MINIO_BUCKET=agent-files-prod",
                "LOG_FORMAT=json",
                _test_sentry_env_line(),
                'APP_CORS_ORIGINS=["https://research.example"]',
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    result = module.validate_env_file(
        env_path,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 8, 7, 19, tzinfo=timezone.utc),
    )
    rendered = json.dumps(module.public_cli_result(result, project_root=tmp_path), ensure_ascii=False)
    check_map = {item["key"]: item for item in result["checks"]}
    action_map = {item["key"]: item for item in result["next_actions"]}

    assert result["status"] == "fail"
    assert result["release_allowed"] is False
    assert check_map["APP_API_KEY_SOURCE"]["status"] == "fail"
    assert check_map["APP_API_KEY_SOURCE"]["source_marker_family"] == "untrusted"
    assert check_map["APP_API_KEY_SOURCE"]["source_provenance_ok"] is False
    assert action_map["APP_API_KEY_SOURCE"]["requires_secret_update"] is True
    assert action_map["APP_API_KEY_SOURCE"]["config_keys"] == ["APP_API_KEY_SOURCE"]
    assert "plain-env:manual-copy" not in rendered
    assert "db-secret" not in rendered
    assert _test_openrouter_key() not in rendered
