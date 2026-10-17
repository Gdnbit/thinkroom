from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "check_production_compose_config.py"
    spec = importlib.util.spec_from_file_location("check_production_compose_config", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_minimal_project(root: Path) -> None:
    (root / "apps" / "backend").mkdir(parents=True)
    (root / "apps" / "frontend").mkdir(parents=True)
    (root / "infra" / "postgres").mkdir(parents=True)
    (root / "apps" / "backend" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (root / "apps" / "frontend" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (root / "infra" / "postgres" / "init.sql").write_text("-- noop\n", encoding="utf-8")
    (root / "docker-compose.prod.yml").write_text(
        "\n".join(
            [
                "services:",
                "  backend:",
                "    build:",
                "      context: ./apps/backend",
                "      dockerfile: Dockerfile",
                "    env_file:",
                "      - ./apps/backend/.env.prod",
                "    environment:",
                "      APP_ENV: production",
                "      DATABASE_URL: postgresql://agent_user:${POSTGRES_PASSWORD}@postgres:5432/agent_db",
                "      DATABASE_URL_SOURCE: ${DATABASE_URL_SOURCE:?required}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "apps" / "backend" / ".env.prod.example").write_text(
        "\n".join(
            [
                "POSTGRES_PASSWORD=compose-secret-that-must-not-leak",
                "DATABASE_URL_SOURCE=sops:DATABASE_URL",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_compose_config_check_uses_temp_private_env_without_leaking_values(tmp_path):
    module = _load_module()
    _write_minimal_project(tmp_path)
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        cwd = Path(kwargs["cwd"])
        assert (cwd / "apps" / "backend" / ".env.prod").read_text(
            encoding="utf-8"
        ) == (tmp_path / "apps" / "backend" / ".env.prod.example").read_text(
            encoding="utf-8"
        )
        assert command == [
            "docker",
            "compose",
            "--env-file",
            "apps/backend/.env.prod",
            "-f",
            "docker-compose.prod.yml",
            "config",
            "--quiet",
        ]
        return CompletedProcess(command, 0, "", "")

    result = module.check_compose_config(
        project_root=tmp_path,
        compose_file=tmp_path / "docker-compose.prod.yml",
        env_file=tmp_path / "apps" / "backend" / ".env.prod.example",
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 1, 0, tzinfo=timezone.utc),
        runner=fake_runner,
    )
    rendered = json.dumps(result, ensure_ascii=False)
    latest = tmp_path / "docs" / "verification" / "production-compose-config-latest.json"

    assert result["status"] == "pass"
    assert result["compose_config_ok"] is True
    assert result["env_file"] == "apps/backend/.env.prod.example"
    assert result["temp_service_env_file"] == "apps/backend/.env.prod"
    assert latest.exists()
    assert json.loads(latest.read_text(encoding="utf-8")) == result
    assert "compose-secret-that-must-not-leak" not in rendered
    assert str(tmp_path) not in rendered
    assert calls


def test_compose_config_check_sanitizes_failure_output(tmp_path):
    module = _load_module()
    _write_minimal_project(tmp_path)

    def fake_runner(command, **kwargs):
        cwd = Path(kwargs["cwd"])
        return CompletedProcess(
            command,
            1,
            "",
            f"failed at {cwd} with compose-secret-that-must-not-leak",
        )

    result = module.check_compose_config(
        project_root=tmp_path,
        compose_file=tmp_path / "docker-compose.prod.yml",
        env_file=tmp_path / "apps" / "backend" / ".env.prod.example",
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 1, 5, tzinfo=timezone.utc),
        runner=fake_runner,
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "fail"
    assert result["compose_config_ok"] is False
    assert result["exit_code"] == 1
    assert "<temp-workspace>" in result["stderr_excerpt"]
    assert "compose-secret-that-must-not-leak" not in rendered
    assert str(tmp_path) not in rendered


def test_missing_production_env_file_reports_materialization_action(tmp_path):
    module = _load_module()
    _write_minimal_project(tmp_path)
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, "", "")

    result = module.check_compose_config(
        project_root=tmp_path,
        compose_file=tmp_path / "docker-compose.prod.yml",
        env_file=tmp_path / "apps" / "backend" / ".env.prod",
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 1, 10, tzinfo=timezone.utc),
        runner=fake_runner,
    )

    assert result["status"] == "fail"
    assert result["compose_config_ok"] is False
    assert result["checks"] == [
        {
            "key": "env_file",
            "status": "fail",
            "reason": "missing",
            "path": "apps/backend/.env.prod",
        }
    ]
    assert calls == []
    assert result["next_actions"] == [
        {
            "key": "production_compose_config",
            "label": "Missing production env candidate",
            "status": "fail",
            "category": "production_compose_config",
            "reason": "env_file_missing",
            "action": (
                "Materialize the approved apps/backend/.env.prod candidate from SOPS or the deployment "
                "secret store, validate it, then rerun the production compose config check."
            ),
            "owner": "deployment_operator",
            "can_auto_fix": False,
            "requires_secret_update": True,
            "production_required": True,
            "config_keys": [],
            "verification_commands": [
                "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error",
                "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
                "python scripts/check_production_compose_config.py --env-file apps/backend/.env.prod --fail-on-error",
            ],
        }
    ]
