from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "run_production_readiness_refresh.py"
    spec = importlib.util.spec_from_file_location("run_production_readiness_refresh", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _passing_frontend_runner(report_dir: Path):
    def fake_frontend_checks(**_kwargs):
        return {
            "status": "pass",
            "release_allowed": True,
            "latest_path": str(report_dir / "frontend-release-checks-latest.json"),
            "failed_commands": [],
            "next_actions": [],
        }

    return fake_frontend_checks


def test_refresh_rejects_template_candidate_env_before_running_substeps(tmp_path):
    module = _load_module()
    calls: list[str] = []
    env_file = tmp_path / "apps" / "backend" / ".env.prod.example"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def should_not_run(**_kwargs):
        calls.append("unexpected")
        return {"status": "pass"}

    result = module.refresh_production_readiness(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 7, 0, tzinfo=timezone.utc),
        release_gate_runner=should_not_run,
        operator_preflight_runner=should_not_run,
        stack_release_runner=should_not_run,
        summary_runner=should_not_run,
    )

    latest = tmp_path / "docs" / "verification" / "production-readiness-refresh-latest.json"
    rendered = json.dumps(result, ensure_ascii=False)
    assert calls == []
    assert result["status"] == "blocked"
    assert result["release_candidate_ready"] is False
    assert result["blocking_steps"] == ["candidate_env_file"]
    assert result["candidate_env_mismatch"] == {
        "expected_env_file": "apps/backend/.env.prod",
        "actual_env_file": "apps/backend/.env.prod.example",
    }
    assert result["next_actions"][0]["key"] == "candidate_env_file"
    assert "apps/backend/.env.prod.example" in result["next_actions"][0]["action"]
    assert "apps/backend/.env.prod" in result["next_actions"][0]["action"]
    assert result["next_actions"][0]["verification_commands"][:2] == [
        "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error",
        "python scripts/materialize_production_env.py --overwrite --source-label sops:production --fail-on-error",
    ]
    assert latest.exists()
    assert json.loads(latest.read_text(encoding="utf-8")) == result
    assert str(tmp_path) not in rendered


def test_refresh_runs_required_report_generators_in_order_with_standard_env(tmp_path):
    module = _load_module()
    calls: list[str] = []
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_release_gate(**kwargs):
        calls.append(f"release_gate:{Path(kwargs['env_file']).name}:{kwargs['require_env_file']}")
        return {
            "status": "pass",
            "release_allowed": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "release-gate-latest.json"),
            "blocking_steps": [],
            "required_actions": [],
        }

    def fake_operator_preflight(**kwargs):
        calls.append(f"operator:{Path(kwargs['env_file']).name}:{kwargs['include_live_gate']}")
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-operator-preflight-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_stack_release(**kwargs):
        calls.append(f"stack:{Path(kwargs['env_file']).name}:{kwargs['execute']}")
        return {
            "status": "planned",
            "release_candidate_ready": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-stack-release-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_frontend_checks(**kwargs):
        calls.append(f"frontend:{Path(kwargs['out_dir']).name}")
        return {
            "status": "pass",
            "release_allowed": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "frontend-release-checks-latest.json"),
            "failed_commands": [],
            "next_actions": [],
        }

    def fake_summary(**kwargs):
        calls.append(f"summary:{Path(kwargs['report_dir']).name}")
        return {
            "status": "pass",
            "readiness_state": "release_ready",
            "release_allowed": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-readiness-summary-latest.json"),
            "digest_latest_path": str(
                tmp_path / "docs" / "verification" / "production-readiness-digest-latest.json"
            ),
            "blocking_reports": [],
            "missing_reports": [],
            "stale_reports": [],
            "candidate_env_mismatch_count": 0,
        }

    result = module.refresh_production_readiness(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        backend_url="http://127.0.0.1:8000",
        now=datetime(2026, 7, 9, 7, 5, tzinfo=timezone.utc),
        release_gate_runner=fake_release_gate,
        operator_preflight_runner=fake_operator_preflight,
        stack_release_runner=fake_stack_release,
        frontend_release_checks_runner=fake_frontend_checks,
        summary_runner=fake_summary,
    )

    assert calls == [
        "release_gate:.env.prod:True",
        "operator:.env.prod:False",
        "stack:.env.prod:False",
        "frontend:verification",
        "summary:verification",
    ]
    assert result["status"] == "pass"
    assert result["release_candidate_ready"] is True
    assert result["readiness_state"] == "release_ready"
    assert result["blocking_steps"] == []
    assert [step["key"] for step in result["steps"]] == [
        "release_gate",
        "production_operator_preflight",
        "production_stack_release",
        "frontend_release_checks",
        "production_readiness_summary",
        "verification_report_hygiene",
    ]
    assert result["refresh_command_plan"] == [
        "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod",
        "python scripts/run_frontend_release_checks.py --fail-on-error",
        "python scripts/summarize_production_readiness.py --report-dir docs/verification --fail-on-blocked",
        "python scripts/check_verification_reports.py docs/verification --fail-on-issue",
    ]
    assert result["env_file"] == "apps/backend/.env.prod"
    hygiene_step = result["steps"][-1]
    hygiene_latest = tmp_path / hygiene_step["latest_path"]
    assert hygiene_step["key"] == "verification_report_hygiene"
    assert hygiene_latest.exists()
    hygiene_report = json.loads(hygiene_latest.read_text(encoding="utf-8"))
    assert hygiene_report["report_type"] == "verification_report_hygiene"
    assert hygiene_report["status"] == "pass"


def test_refresh_blocks_when_final_hygiene_catches_summary_artifact_regression(tmp_path):
    module = _load_module()
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    report_dir = tmp_path / "docs" / "verification"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_release_gate(**_kwargs):
        return {
            "status": "pass",
            "release_allowed": True,
            "latest_path": str(report_dir / "release-gate-latest.json"),
            "blocking_steps": [],
            "required_actions": [],
        }

    def fake_operator_preflight(**_kwargs):
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "latest_path": str(report_dir / "production-operator-preflight-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_stack_release(**_kwargs):
        return {
            "status": "planned",
            "release_candidate_ready": True,
            "latest_path": str(report_dir / "production-stack-release-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_summary(**_kwargs):
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "production-readiness-action-plan-latest.md").write_text(
            "Rerun against http://127.0.0.1:8000 before release.\n",
            encoding="utf-8",
        )
        return {
            "status": "pass",
            "readiness_state": "release_ready",
            "release_allowed": True,
            "latest_path": str(report_dir / "production-readiness-summary-latest.json"),
            "digest_latest_path": str(report_dir / "production-readiness-digest-latest.json"),
            "blocking_reports": [],
            "missing_reports": [],
            "stale_reports": [],
            "candidate_env_mismatch_count": 0,
        }

    result = module.refresh_production_readiness(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=report_dir,
        backend_url="http://127.0.0.1:8000",
        now=datetime(2026, 7, 9, 7, 6, tzinfo=timezone.utc),
        release_gate_runner=fake_release_gate,
        operator_preflight_runner=fake_operator_preflight,
        stack_release_runner=fake_stack_release,
        frontend_release_checks_runner=_passing_frontend_runner(report_dir),
        summary_runner=fake_summary,
    )

    rendered = json.dumps(result, ensure_ascii=False)
    hygiene_step = result["steps"][-1]
    hygiene_action = result["next_actions"][0]
    assert result["status"] == "blocked"
    assert result["release_candidate_ready"] is False
    assert result["blocking_steps"] == ["verification_report_hygiene"]
    assert hygiene_step["key"] == "verification_report_hygiene"
    assert hygiene_step["status"] == "fail"
    assert hygiene_step["exit_code"] == 2
    assert hygiene_action["key"] == "verification_report_hygiene"
    assert hygiene_action["verification_commands"] == [
        "python scripts/check_verification_reports.py docs/verification --fail-on-issue",
    ]
    assert "127.0.0.1" not in rendered
    assert "localhost" not in rendered


def test_refresh_exit_code_fails_on_blocked_summary_only_when_requested(tmp_path):
    module = _load_module()

    assert module.refresh_exit_code({"status": "blocked"}, fail_on_blocked=False) == 0
    assert module.refresh_exit_code({"status": "blocked"}, fail_on_blocked=True) == 2
    assert module.refresh_exit_code({"status": "pass"}, fail_on_blocked=True) == 0


def test_refresh_coalesces_duplicate_actions_without_losing_sources(tmp_path):
    module = _load_module()
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    duplicate_action = {
        "key": "production_compose_config",
        "label": "Missing production env candidate",
        "status": "fail",
        "category": "production_compose_config",
        "reason": "env_file_missing",
        "action": "Materialize apps/backend/.env.prod, validate it, then rerun compose config.",
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

    def fake_release_gate(**_kwargs):
        return {
            "status": "blocked",
            "release_allowed": False,
            "latest_path": str(tmp_path / "docs" / "verification" / "release-gate-latest.json"),
            "blocking_steps": ["production_compose_config"],
            "required_actions": [duplicate_action],
        }

    def fake_operator_preflight(**_kwargs):
        return {
            "status": "blocked",
            "release_candidate_ready": False,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-operator-preflight-latest.json"),
            "blocking_steps": ["production_compose_config"],
            "next_actions": [{**duplicate_action, "source_report": "production_operator_preflight"}],
        }

    def fake_stack_release(**_kwargs):
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-stack-release-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_summary(**_kwargs):
        return {
            "status": "blocked",
            "readiness_state": "blocked",
            "release_allowed": False,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-readiness-summary-latest.json"),
            "digest_latest_path": str(tmp_path / "docs" / "verification" / "production-readiness-digest-latest.json"),
            "blocking_reports": ["production_compose_config"],
            "missing_reports": [],
            "stale_reports": [],
            "candidate_env_mismatch_count": 0,
            "next_actions": [{**duplicate_action, "source_report": "production_readiness_summary"}],
        }

    result = module.refresh_production_readiness(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 7, 10, tzinfo=timezone.utc),
        release_gate_runner=fake_release_gate,
        operator_preflight_runner=fake_operator_preflight,
        stack_release_runner=fake_stack_release,
        frontend_release_checks_runner=_passing_frontend_runner(tmp_path / "docs" / "verification"),
        summary_runner=fake_summary,
    )

    compose_actions = [action for action in result["next_actions"] if action["key"] == "production_compose_config"]
    assert len(compose_actions) == 1
    assert result["next_action_count"] == 1
    assert compose_actions[0]["source_step"] == "release_gate"
    assert compose_actions[0]["source_steps"] == [
        "release_gate",
        "production_operator_preflight",
        "production_readiness_summary",
    ]
    assert compose_actions[0]["source_reports"] == [
        "production_operator_preflight",
        "production_readiness_summary",
    ]
    assert compose_actions[0]["source_count"] == 3


def test_refresh_coalesces_same_operator_action_with_different_metadata(tmp_path):
    module = _load_module()
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    action_text = "本地 MVP 可继续；正式发布前切换 APP_ENV=production，并确保 production_config_errors 为空。"
    runtime_action = {
        "key": "runtime_profile",
        "label": "部署 Profile",
        "status": "warn",
        "action": action_text,
        "owner": "deployment_operator",
        "can_auto_fix": False,
        "requires_secret_update": False,
        "production_required": True,
        "verification": [
            {
                "kind": "endpoint",
                "target": "/api/v1/health/production-preflight",
                "expectation": "production_dry_run.status=pass",
            }
        ],
    }

    def fake_release_gate(**_kwargs):
        return {
            "status": "blocked",
            "release_allowed": False,
            "latest_path": str(tmp_path / "docs" / "verification" / "release-gate-latest.json"),
            "blocking_steps": ["runtime_profile"],
            "required_actions": [runtime_action],
        }

    def fake_operator_preflight(**_kwargs):
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-operator-preflight-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_stack_release(**_kwargs):
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-stack-release-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_summary(**_kwargs):
        return {
            "status": "blocked",
            "readiness_state": "blocked",
            "release_allowed": False,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-readiness-summary-latest.json"),
            "digest_latest_path": str(tmp_path / "docs" / "verification" / "production-readiness-digest-latest.json"),
            "blocking_reports": ["runtime_profile"],
            "missing_reports": [],
            "stale_reports": [],
            "candidate_env_mismatch_count": 0,
            "next_actions": [
                {
                    **runtime_action,
                    "category": "production_profile",
                    "source_report": "production_readiness_summary",
                }
            ],
        }

    result = module.refresh_production_readiness(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 7, 20, tzinfo=timezone.utc),
        release_gate_runner=fake_release_gate,
        operator_preflight_runner=fake_operator_preflight,
        stack_release_runner=fake_stack_release,
        frontend_release_checks_runner=_passing_frontend_runner(tmp_path / "docs" / "verification"),
        summary_runner=fake_summary,
    )

    runtime_actions = [action for action in result["next_actions"] if action["key"] == "runtime_profile"]
    assert len(runtime_actions) == 1
    assert result["next_action_count"] == 1
    assert runtime_actions[0]["category"] == "production_profile"
    assert runtime_actions[0]["source_steps"] == [
        "release_gate",
        "production_readiness_summary",
    ]
    assert runtime_actions[0]["source_reports"] == ["production_readiness_summary"]
    assert runtime_actions[0]["source_count"] == 2


def test_refresh_coalesces_same_config_action_when_description_differs(tmp_path):
    module = _load_module()
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    full_action_text = (
        "Set RATE_LIMIT_STORAGE_URI to a shared Redis URI such as redis://redis:6379/1, "
        "enable REDIS_ENABLED=true, and rerun the production preflight."
    )
    truncated_action_text = "1, enable REDIS_ENABLED=true, and rerun the production preflight."
    base_action = {
        "key": "rate_limit_storage",
        "label": "Rate limit storage",
        "status": "warn",
        "owner": "release_operator",
        "can_auto_fix": False,
        "requires_secret_update": False,
        "production_required": True,
        "config_keys": ["RATE_LIMIT_STORAGE_URI", "REDIS_ENABLED"],
        "verification_commands": [
            "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000"
        ],
    }

    def fake_release_gate(**_kwargs):
        return {
            "status": "blocked",
            "release_allowed": False,
            "latest_path": str(tmp_path / "docs" / "verification" / "release-gate-latest.json"),
            "blocking_steps": ["production_preflight"],
            "required_actions": [{**base_action, "action": full_action_text}],
        }

    def fake_operator_preflight(**_kwargs):
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-operator-preflight-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_stack_release(**_kwargs):
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-stack-release-latest.json"),
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_summary(**_kwargs):
        return {
            "status": "blocked",
            "readiness_state": "blocked",
            "release_allowed": False,
            "latest_path": str(tmp_path / "docs" / "verification" / "production-readiness-summary-latest.json"),
            "digest_latest_path": str(tmp_path / "docs" / "verification" / "production-readiness-digest-latest.json"),
            "blocking_reports": ["production_preflight"],
            "missing_reports": [],
            "stale_reports": [],
            "candidate_env_mismatch_count": 0,
            "next_actions": [
                {
                    **base_action,
                    "action": truncated_action_text,
                    "config_keys": ["RATE_LIMIT_STORAGE_URI"],
                    "source_report": "release_gate",
                    "source_step": "production_preflight",
                    "split_config_key": "RATE_LIMIT_STORAGE_URI",
                    "split_from_action_key": "rate_limit_storage",
                    "split_from_config_keys": ["RATE_LIMIT_STORAGE_URI", "REDIS_ENABLED"],
                },
                {
                    **base_action,
                    "action": truncated_action_text,
                    "config_keys": ["REDIS_ENABLED"],
                    "source_report": "release_gate",
                    "source_step": "production_preflight",
                    "split_config_key": "REDIS_ENABLED",
                    "split_from_action_key": "rate_limit_storage",
                    "split_from_config_keys": ["RATE_LIMIT_STORAGE_URI", "REDIS_ENABLED"],
                }
            ],
        }

    result = module.refresh_production_readiness(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 7, 30, tzinfo=timezone.utc),
        release_gate_runner=fake_release_gate,
        operator_preflight_runner=fake_operator_preflight,
        stack_release_runner=fake_stack_release,
        frontend_release_checks_runner=_passing_frontend_runner(tmp_path / "docs" / "verification"),
        summary_runner=fake_summary,
    )

    rate_limit_actions = [action for action in result["next_actions"] if action["key"] == "rate_limit_storage"]
    assert len(rate_limit_actions) == 1
    assert result["next_action_count"] == 1
    assert rate_limit_actions[0]["action"] == full_action_text
    assert rate_limit_actions[0]["verification_commands"] == [
        "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url [backend-url]"
    ]
    assert rate_limit_actions[0]["source_steps"] == ["release_gate", "production_readiness_summary"]
    assert rate_limit_actions[0]["source_reports"] == ["release_gate"]
    assert rate_limit_actions[0]["source_count"] == 3
    assert "127.0.0.1" not in json.dumps(result, ensure_ascii=False)
