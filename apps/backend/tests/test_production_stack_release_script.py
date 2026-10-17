from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "run_production_stack_release.py"
    spec = importlib.util.spec_from_file_location("run_production_stack_release", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeHttpResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_wait_for_backend_health_requires_ready_payload_and_exports_worker_summary(monkeypatch):
    module = _load_module()
    payload = {
        "status": "ready",
        "checks": {
            "ingestion_worker": {
                "ok": True,
                "required": True,
                "mode": "celery",
                "durable": True,
                "worker_reachable": True,
                "worker_count": 1,
            }
        },
        "deployment_profile": {"blocking_checks": []},
    }
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeHttpResponse(200, payload),
    )

    result = module.wait_for_backend_health(backend_url="http://backend.invalid", timeout=0.1)

    assert result["status"] == "pass"
    assert result["runtime_status"] == "ready"
    assert result["blocking_checks"] == []
    assert result["ingestion_worker"] == {
        "required": True,
        "mode": "celery",
        "durable": True,
        "worker_reachable": True,
        "worker_count": 1,
    }


def test_wait_for_backend_health_rejects_http_200_degraded_payload(monkeypatch):
    module = _load_module()
    payload = {
        "status": "degraded",
        "checks": {
            "ingestion_worker": {
                "ok": False,
                "required": True,
                "mode": "celery",
                "durable": True,
                "worker_reachable": False,
                "worker_count": 0,
            }
        },
        "deployment_profile": {"blocking_checks": ["ingestion_worker"]},
    }
    monotonic_values = iter([0.0, 0.0, 0.02, 0.02])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeHttpResponse(200, payload),
    )

    result = module.wait_for_backend_health(backend_url="http://backend.invalid", timeout=0.01)

    assert result["status"] == "fail"
    assert result["last_error"] == "readiness status degraded"
    assert result["runtime_status"] == "degraded"
    assert result["blocking_checks"] == ["ingestion_worker"]
    assert result["ingestion_worker"]["worker_reachable"] is False


def test_stack_release_plans_without_docker_side_effects_after_static_preflight(tmp_path):
    module = _load_module()
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    calls: list[str] = []

    def fake_operator_preflight(**kwargs):
        calls.append(f"preflight:{kwargs['include_live_gate']}")
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "blocking_steps": [],
            "next_actions": [],
            "export_path": str(tmp_path / "docs" / "verification" / "production-operator-preflight.json"),
            "latest_path": str(tmp_path / "docs" / "verification" / "production-operator-preflight-latest.json"),
        }

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("dry-run planning must not run docker commands")

    def forbidden_health_checker(*_args, **_kwargs):
        raise AssertionError("dry-run planning must not poll backend health")

    result = module.run_stack_release(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 4, 0, tzinfo=timezone.utc),
        execute=False,
        operator_preflight_runner=fake_operator_preflight,
        command_runner=forbidden_runner,
        health_checker=forbidden_health_checker,
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert calls == ["preflight:False"]
    assert result["status"] == "planned"
    assert result["release_candidate_ready"] is True
    assert result["release_executed"] is False
    assert result["execution_skipped_reason"] == "dry_run_plan_only"
    assert result["blocking_steps"] == []
    assert [step["key"] for step in result["steps"]] == ["static_operator_preflight"]
    assert result["release_command_plan"] == [
        "docker compose --env-file <candidate-production-env> -f docker-compose.prod.yml build",
        "docker compose --env-file <candidate-production-env> -f docker-compose.prod.yml run --rm migrate",
        "docker compose --env-file <candidate-production-env> -f docker-compose.prod.yml up -d",
        "wait <backend-url>/health/ready",
        "python scripts/run_production_operator_preflight.py --env-file <candidate-production-env> --include-live-gate --backend-url <backend-url> --fail-on-blocked",
    ]
    assert result["env_file"] == "apps/backend/.env.prod"
    assert result["compose_file"] == "docker-compose.prod.yml"
    assert result["backend_url"] == "<backend-url>"
    assert result["latest_path"] == "docs/verification/production-stack-release-latest.json"
    assert str(tmp_path) not in rendered


def test_stack_release_skips_execution_when_static_preflight_blocks(tmp_path):
    module = _load_module()
    env_file = tmp_path / "candidate.env"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    calls: list[str] = []

    def fake_operator_preflight(**kwargs):
        calls.append(f"preflight:{kwargs['include_live_gate']}")
        return {
            "status": "blocked",
            "release_candidate_ready": False,
            "blocking_steps": ["production_env_validation"],
            "next_actions": [
                {
                    "key": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "requires_secret_update": True,
                    "config_keys": ["DATABASE_URL"],
                }
            ],
        }

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("blocked static preflight must prevent docker commands")

    result = module.run_stack_release(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 4, 5, tzinfo=timezone.utc),
        execute=True,
        operator_preflight_runner=fake_operator_preflight,
        command_runner=forbidden_runner,
    )

    assert calls == ["preflight:False"]
    assert result["status"] == "blocked"
    assert result["release_candidate_ready"] is False
    assert result["release_executed"] is False
    assert result["execution_skipped_reason"] == "static_preflight_failed"
    assert result["blocking_steps"] == ["static_operator_preflight"]
    assert result["next_action_count"] == 1
    assert result["next_actions"][0]["source_step"] == "static_operator_preflight"
    assert result["next_actions"][0]["key"] == "DATABASE_URL"


def test_stack_release_executes_build_migrate_up_health_and_live_gate(tmp_path):
    module = _load_module()
    env_file = tmp_path / "apps" / "backend" / ".env.prod"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    commands: list[list[str]] = []
    preflight_calls: list[str] = []

    def fake_operator_preflight(**kwargs):
        preflight_calls.append(f"preflight:{kwargs['include_live_gate']}")
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_runner(command, **_kwargs):
        commands.append(list(command))
        return FakeCompletedProcess(returncode=0, stdout="ok", stderr="")

    def fake_health_checker(**kwargs):
        return {
            "key": "backend_health",
            "label": "Backend health",
            "status": "pass",
            "url": "<backend-url>/health/ready",
            "attempts": 1,
            "elapsed_seconds": 0.01,
        }

    result = module.run_stack_release(
        env_file=env_file,
        backend_url="http://127.0.0.1:8000",
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 4, 10, tzinfo=timezone.utc),
        execute=True,
        operator_preflight_runner=fake_operator_preflight,
        command_runner=fake_runner,
        health_checker=fake_health_checker,
    )

    assert preflight_calls == ["preflight:False", "preflight:True"]
    assert commands == [
        ["docker", "compose", "--env-file", str(env_file), "-f", str(tmp_path / "docker-compose.prod.yml"), "build"],
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(tmp_path / "docker-compose.prod.yml"),
            "run",
            "--rm",
            "migrate",
        ],
        ["docker", "compose", "--env-file", str(env_file), "-f", str(tmp_path / "docker-compose.prod.yml"), "up", "-d"],
    ]
    assert result["status"] == "pass"
    assert result["release_candidate_ready"] is True
    assert result["release_executed"] is True
    assert result["blocking_steps"] == []
    assert [step["key"] for step in result["steps"]] == [
        "static_operator_preflight",
        "docker_build",
        "docker_migrate",
        "docker_up",
        "backend_health",
        "live_operator_preflight",
    ]


def test_stack_release_redacts_command_output_secret_shapes(tmp_path):
    module = _load_module()
    env_file = tmp_path / "candidate.env"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    def fake_operator_preflight(**_kwargs):
        return {
            "status": "pass",
            "release_candidate_ready": True,
            "blocking_steps": [],
            "next_actions": [],
        }

    def fake_runner(_command, **_kwargs):
        return FakeCompletedProcess(
            returncode=1,
            stdout="using sk-or-v1-" + ("A" * 24),
            stderr=f"failed at {tmp_path} " + "postgresql://user:" + "password@example.invalid/db",
        )

    result = module.run_stack_release(
        env_file=env_file,
        project_root=tmp_path,
        out_dir=tmp_path / "docs" / "verification",
        now=datetime(2026, 7, 9, 4, 15, tzinfo=timezone.utc),
        execute=True,
        operator_preflight_runner=fake_operator_preflight,
        command_runner=fake_runner,
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "blocked"
    assert result["blocking_steps"] == ["docker_build"]
    assert "sk-or-v1-" not in rendered
    assert "postgresql://user:" + "password@" not in rendered
    assert str(tmp_path) not in rendered
    assert "[redacted-openrouter-key]" in rendered
    assert "[redacted-credential-uri]" in rendered
