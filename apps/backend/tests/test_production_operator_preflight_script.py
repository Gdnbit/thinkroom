from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "run_production_operator_preflight.py"
    spec = importlib.util.spec_from_file_location("run_production_operator_preflight", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operator_preflight_runs_static_checks_and_writes_sanitized_report(tmp_path):
    module = _load_module()
    calls: list[str] = []
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_env_validator(_env_file, **kwargs):
        calls.append(f"env:{Path(_env_file).name}")
        out_dir = Path(kwargs["out_dir"])
        return {
            "status": "pass",
            "release_allowed": True,
            "export_path": str(out_dir / "production-env-validation-20260709T030000Z.json"),
            "latest_path": str(out_dir / "production-env-validation-latest.json"),
            "next_actions": [],
        }

    def fake_compose_checker(**kwargs):
        calls.append(f"compose:{Path(kwargs['env_file']).name}")
        out_dir = Path(kwargs["out_dir"])
        return {
            "status": "pass",
            "compose_config_ok": True,
            "export_path": str(out_dir / "production-compose-config-20260709T030000Z.json"),
            "latest_path": str(out_dir / "production-compose-config-latest.json"),
            "next_actions": [],
        }

    def fake_live_gate(**_kwargs):
        calls.append("live-gate")
        return {"status": "pass", "release_allowed": True, "next_actions": []}

    result = module.run_operator_preflight(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 3, 0, tzinfo=timezone.utc),
        env_validator=fake_env_validator,
        compose_checker=fake_compose_checker,
        live_gate_runner=fake_live_gate,
    )
    latest = tmp_path / "docs" / "verification" / "production-operator-preflight-latest.json"
    exported = tmp_path / "docs" / "verification" / "production-operator-preflight-20260709T030000Z.json"
    rendered = json.dumps(result, ensure_ascii=False)

    assert calls == ["env:.env.prod", "compose:.env.prod"]
    assert result["status"] == "pass"
    assert result["release_candidate_ready"] is True
    assert result["live_gate_included"] is False
    assert result["blocking_steps"] == []
    assert [step["key"] for step in result["steps"]] == [
        "production_env_validation",
        "production_compose_config",
    ]
    assert result["operator_command_plan"] == [
        "python scripts/validate_production_env.py --env-file <candidate-production-env> --fail-on-error",
        "python scripts/check_production_compose_config.py --env-file <candidate-production-env> --fail-on-error",
        "python scripts/run_production_operator_preflight.py --env-file <candidate-production-env> --include-live-gate --backend-url <backend-url>",
    ]
    assert latest.exists()
    assert exported.exists()
    assert json.loads(latest.read_text(encoding="utf-8")) == result
    assert str(tmp_path) not in rendered
    assert result["env_file"] == "apps/backend/.env.prod"
    assert result["export_path"] == "docs/verification/production-operator-preflight-20260709T030000Z.json"
    assert result["latest_path"] == "docs/verification/production-operator-preflight-latest.json"


def test_operator_preflight_blocks_and_groups_static_actions_without_live_gate(tmp_path):
    module = _load_module()
    calls: list[str] = []
    env_file = tmp_path / "candidate.env"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_env_validator(_env_file, **_kwargs):
        calls.append("env")
        return {
            "status": "fail",
            "release_allowed": False,
            "next_actions": [
                {
                    "key": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "owner": "deployment_operator",
                    "requires_secret_update": True,
                    "config_keys": ["DATABASE_URL"],
                }
            ],
        }

    def fake_compose_checker(**_kwargs):
        calls.append("compose")
        return {"status": "pass", "compose_config_ok": True, "next_actions": []}

    def fake_live_gate(**_kwargs):
        calls.append("live-gate")
        return {"status": "pass", "release_allowed": True, "next_actions": []}

    result = module.run_operator_preflight(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 3, 5, tzinfo=timezone.utc),
        include_live_gate=True,
        env_validator=fake_env_validator,
        compose_checker=fake_compose_checker,
        live_gate_runner=fake_live_gate,
    )

    assert calls == ["env", "compose"]
    assert result["status"] == "blocked"
    assert result["release_candidate_ready"] is False
    assert result["live_gate_included"] is False
    assert result["live_gate_skipped_reason"] == "static_checks_failed"
    assert result["blocking_steps"] == ["production_env_validation"]
    assert result["next_action_count"] == 1
    assert result["next_actions"][0]["source_step"] == "production_env_validation"
    assert result["next_actions"][0]["key"] == "DATABASE_URL"


def test_operator_preflight_can_include_live_release_gate_after_static_checks(tmp_path):
    module = _load_module()
    calls: list[str] = []
    env_file = tmp_path / "candidate.env"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_env_validator(_env_file, **_kwargs):
        calls.append("env")
        return {"status": "pass", "release_allowed": True, "next_actions": []}

    def fake_compose_checker(**_kwargs):
        calls.append("compose")
        return {"status": "pass", "compose_config_ok": True, "next_actions": []}

    def fake_live_gate(**kwargs):
        calls.append(f"live:{Path(kwargs['env_file']).name}:{kwargs['backend_url']}")
        return {"status": "pass", "release_allowed": True, "next_actions": []}

    result = module.run_operator_preflight(
        env_file=env_file,
        backend_url="http://127.0.0.1:8000",
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 3, 10, tzinfo=timezone.utc),
        include_live_gate=True,
        env_validator=fake_env_validator,
        compose_checker=fake_compose_checker,
        live_gate_runner=fake_live_gate,
    )

    assert calls == ["env", "compose", "live:candidate.env:http://127.0.0.1:8000"]
    assert result["status"] == "pass"
    assert result["release_candidate_ready"] is True
    assert result["live_gate_included"] is True
    assert result["live_gate_skipped_reason"] == ""
    assert [step["key"] for step in result["steps"]] == [
        "production_env_validation",
        "production_compose_config",
        "release_gate",
    ]
