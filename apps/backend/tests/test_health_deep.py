"""P1-1 深度健康检查测试（2026-04-24）

覆盖：
- /health 与 /health/live 总是 200 + status=ok
- /health/ready 的状态机：
  - DB OK + OpenRouter key 为空 → 200 ready 或 degraded（外部依赖降级）
  - DB fail → 503 unhealthy
- X-Request-Id 响应头被正确塞入
"""
from __future__ import annotations

import json

import pytest
from datetime import datetime, timedelta, timezone


def _test_redis_uri(db: int = 0) -> str:
    return "redis://redis-user:" + "redis-pass" + f"@redis:6379/{db}"


def _test_sentry_dsn() -> str:
    return "https://public" + "@example.ingest.sentry.io/123"


def test_redis_probe_distinguishes_disabled_unavailable_and_ready(monkeypatch):
    from app.api.v1 import health_deep
    from app.config import settings
    from app.services import cache

    monkeypatch.setattr(settings, "REDIS_ENABLED", False)
    assert health_deep._check_redis() == {
        "ok": False,
        "configured": False,
        "error": "not configured",
    }

    monkeypatch.setattr(settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(cache, "_get_client", lambda: None)
    assert health_deep._check_redis() == {
        "ok": False,
        "configured": True,
        "error": "unavailable",
    }

    monkeypatch.setattr(settings, "REDIS_URL", _test_redis_uri())
    monkeypatch.setattr(cache, "_get_client", lambda: type("Redis", (), {"ping": lambda self: True})())
    ready = health_deep._check_redis()
    assert ready == {"configured": True, "ping": True}
    assert "redis-pass" not in json.dumps(ready)


# ── liveness（不依赖下游） ────────────────────────────────────────


class TestHealthLive:
    def test_health_root_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "timestamp" in body
        assert "env" in body

    def test_health_live_alias_ok(self, client):
        r = client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_request_id_header_present(self, client):
        """X-Request-Id 永远会被注入响应头。"""
        r = client.get("/health/live")
        assert r.headers.get("X-Request-Id"), "missing X-Request-Id header"

    def test_request_id_echo(self, client):
        """客户端传入 X-Request-Id 会被透传（便于跨服务追踪）。"""
        rid = "test-rid-abc123"
        r = client.get("/health/live", headers={"X-Request-Id": rid})
        assert r.headers.get("X-Request-Id") == rid

    def test_api_v1_health_exposes_parser_capabilities(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "DOCUMENT_PARSER_ADAPTER", "current")

        r = client.get("/api/v1/health")

        assert r.status_code == 200
        body = r.json()
        parser_capabilities = body["capabilities"]["document_parser"]
        assert parser_capabilities["active_adapter"] == "current"
        assert parser_capabilities["status"] == "ready"

    def test_api_v1_release_readiness_summarizes_local_profile(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "APP_ENV", "development")
        monkeypatch.setattr(settings, "LOG_FORMAT", "pretty")
        monkeypatch.setattr(settings, "DOCUMENT_PARSER_ADAPTER", "current")

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "warn"
        assert body["deployment_profile"]["mode"] == "local_mvp"
        checks = {item["key"]: item for item in body["checks"]}
        if checks["runtime_readiness"]["status"] == "pass":
            assert checks["runtime_readiness"]["remediation"] == ""
        assert checks["runtime_profile"]["status"] == "warn"
        assert checks["secrets"]["status"] == "warn"
        assert checks["document_parser"]["status"] == "pass"
        assert "SOPS" in checks["secrets"]["remediation"]
        assert "APP_API_KEY" in checks["secrets"]["remediation"]
        assert "SENTRY_DSN" in checks["observability"]["remediation"]
        assert "LOG_FORMAT=json" in checks["observability"]["remediation"]
        observability = checks["observability"]["signals"]
        assert observability["production_posture_ok"] is False
        assert observability["checks"]["metrics_endpoint"]["status"] == "pass"
        assert observability["checks"]["request_correlation"]["status"] == "pass"
        assert observability["checks"]["structured_logs"]["status"] == "warn"
        assert observability["checks"]["error_tracking"]["status"] == "warn"
        assert observability["missing_for_production"] == ["structured_logs", "error_tracking"]
        assert observability["deployment_sources"]["log_format"]["source"] == "default_or_missing"
        assert observability["deployment_sources"]["sentry_dsn"]["source"] == "default_or_missing"
        assert observability["checks"]["structured_logs"]["source"] == "default_or_missing"
        assert observability["checks"]["error_tracking"]["source"] == "default_or_missing"
        assert "start_local_dependencies.ps1" in checks["shared_redis"]["remediation"]
        assert "-UseLocalDependencies" in checks["shared_redis"]["remediation"]
        assert body["summary"]["fail"] == 0
        assert body["summary"]["warn"] >= 1

    def test_api_v1_release_readiness_exposes_safe_production_digest(self, client, tmp_path, monkeypatch):
        from app.services import release_readiness

        report_dir = tmp_path / "docs" / "verification"
        report_dir.mkdir(parents=True)
        digest_path = report_dir / "production-readiness-digest-latest.json"
        digest_path.write_text(
            json.dumps(
                {
                    "report_type": "production_readiness_digest",
                    "status": "blocked",
                    "readiness_state": "blocked",
                    "release_allowed": False,
                    "latest_path": str(digest_path),
                    "blocker_count": 2,
                    "blocker_total_count": 2,
                    "blocker_shown_count": 1,
                    "blocking_report_count": 1,
                    "blocking_reports": ["release_gate"],
                    "missing_report_count": 1,
                    "missing_reports": ["production_preflight"],
                    "stale_report_count": 1,
                    "stale_reports": ["public_api_leak_scan"],
                    "candidate_env_mismatch_count": 1,
                    "candidate_env_mismatches": [
                        {
                            "report_key": "production_env_validation",
                            "report_label": "Production env validation",
                            "field": "env_path",
                            "expected_env_file": "apps/backend/.env.prod",
                            "actual_env_file": str(tmp_path / "apps" / "backend" / ".env.prod.example"),
                            "regenerate_command": (
                                "python scripts/validate_production_env.py "
                                "--env-file apps/backend/.env.prod --fail-on-error"
                            ),
                        }
                    ],
                    "top_remediation_limit": 1,
                    "top_remediation_count": 1,
                    "blockers": [
                        {
                            "group_key": "config:APP_API_KEY",
                            "label": "APP_API_KEY",
                            "section_key": "critical_secrets",
                            "section_label": "Critical Secrets",
                            "priority": 10,
                            "config_keys": ["APP_API_KEY"],
                            "referenced_config_keys": [
                                "APP_API_KEY",
                                "APP_SECRET_KEY",
                                "OPENROUTER_API_KEY",
                            ],
                            "categories": ["secrets"],
                            "source_reports": ["release_gate"],
                            "requires_secret_update": True,
                            "production_required": True,
                            "split_from_multi_key_action_count": 0,
                            "verification_commands": [
                                "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000"
                            ],
                            "sample_actions": [{"action": "do not expose this verbose action"}],
                        }
                    ],
                    "next_actions": [{"action": "do not expose raw actions"}],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        digest = body["production_readiness_digest"]
        rendered = str(digest)
        assert digest["available"] is True
        assert digest["status"] == "blocked"
        assert digest["readiness_state"] == "blocked"
        assert digest["release_allowed"] is False
        assert digest["path"] == "docs/verification/production-readiness-digest-latest.json"
        assert digest["blocker_count"] == 2
        assert digest["blocker_total_count"] == 2
        assert digest["blocker_shown_count"] == 1
        assert digest["top_remediation_count"] == 1
        assert digest["blockers"][0]["group_key"] == "config:APP_API_KEY"
        assert digest["blockers"][0]["referenced_config_keys"] == [
            "APP_API_KEY",
            "APP_SECRET_KEY",
            "OPENROUTER_API_KEY",
        ]
        assert digest["blockers"][0]["requires_secret_update"] is True
        assert digest["candidate_env_mismatch_count"] == 1
        assert digest["candidate_env_mismatches"] == [
            {
                "report_key": "production_env_validation",
                "report_label": "Production env validation",
                "field": "env_path",
                "expected_env_file": "apps/backend/.env.prod",
                "actual_env_file": "apps/backend/.env.prod.example",
                "regenerate_command": (
                    "python scripts/validate_production_env.py "
                    "--env-file apps/backend/.env.prod --fail-on-error"
                ),
            }
        ]
        assert digest["upstream_report_issues"] == [
            {
                "key": "production_preflight",
                "status": "missing",
                "regenerate_command": (
                    "python scripts/export_production_preflight.py "
                    "--backend-url http://127.0.0.1:8000 --fail-on-not-ready"
                ),
            },
            {
                "key": "public_api_leak_scan",
                "status": "stale",
                "regenerate_command": (
                    "python scripts/scan_public_api_leaks.py "
                    "--backend-url http://127.0.0.1:8000 --fail-on-leak"
                ),
            },
        ]
        assert "sample_actions" not in rendered
        assert "next_actions" not in rendered
        assert str(tmp_path) not in rendered

    def test_api_v1_release_readiness_exposes_safe_env_materialization_report(self, client, tmp_path, monkeypatch):
        from app.services import release_readiness

        report_dir = tmp_path / "docs" / "verification"
        report_dir.mkdir(parents=True)
        materialization_path = report_dir / "production-env-materialization-latest.json"
        materialization_path.write_text(
            json.dumps(
                {
                    "report_type": "production_env_materialization",
                    "status": "fail",
                    "generated_at": "2026-07-09T08:30:00+00:00",
                    "dry_run": True,
                    "output_path": str(tmp_path / "apps" / "backend" / ".env.prod"),
                    "output_written": False,
                    "would_write_output": False,
                    "missing_required_key_count": 2,
                    "missing_required_keys": ["APP_API_KEY", "APP_SECRET_KEY"],
                    "missing_config_request_count": 2,
                    "missing_config_requests": [
                        {
                            "config_key": "APP_API_KEY",
                            "suggested_sops_key": "adms_app_api_key",
                            "source_marker_key": "APP_API_KEY_SOURCE",
                            "sensitivity": "secret",
                            "can_be_derived_from": [],
                            "accepted_source_prefixes": ["sops:", "vault:"],
                            "description": "Bearer/API key secret-value-should-not-appear",
                        },
                        {
                            "config_key": "APP_SECRET_KEY",
                            "suggested_sops_key": "adms_app_secret_key",
                            "source_marker_key": "APP_SECRET_KEY_SOURCE",
                            "sensitivity": "secret",
                            "can_be_derived_from": [],
                            "accepted_source_prefixes": ["sops:", "vault:"],
                            "description": "Application signing secret.",
                        },
                    ],
                    "alias_resolution": {
                        "APP_API_KEY": "",
                        "OPENROUTER_API_KEY": "legacy_env_alias",
                    },
                    "verification_commands": [
                        "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error"
                    ],
                    "policy_basis": ["never expose sk-or-v1-sensitive-value"],
                    "latest_path": str(materialization_path),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        materialization = body["production_env_materialization"]
        rendered = json.dumps(materialization, ensure_ascii=False)
        assert body["evidence_sources"]["production_env_materialization_path"] == (
            "docs/verification/production-env-materialization-latest.json"
        )
        assert materialization["available"] is True
        assert materialization["status"] == "fail"
        assert materialization["dry_run"] is True
        assert materialization["output_path"] == "apps/backend/.env.prod"
        assert materialization["output_written"] is False
        assert materialization["would_write_output"] is False
        assert materialization["missing_required_key_count"] == 2
        assert materialization["missing_required_keys"] == ["APP_API_KEY", "APP_SECRET_KEY"]
        assert materialization["missing_config_request_count"] == 2
        assert materialization["missing_config_requests"] == [
            {
                "config_key": "APP_API_KEY",
                "suggested_sops_key": "adms_app_api_key",
                "source_marker_key": "APP_API_KEY_SOURCE",
                "sensitivity": "secret",
                "can_be_derived_from": [],
                "accepted_source_prefixes": ["sops:", "vault:"],
            },
            {
                "config_key": "APP_SECRET_KEY",
                "suggested_sops_key": "adms_app_secret_key",
                "source_marker_key": "APP_SECRET_KEY_SOURCE",
                "sensitivity": "secret",
                "can_be_derived_from": [],
                "accepted_source_prefixes": ["sops:", "vault:"],
            },
        ]
        assert materialization["alias_resolution"] == {
            "APP_API_KEY": "",
            "OPENROUTER_API_KEY": "legacy_env_alias",
        }
        assert materialization["operator_commands"] == [
            "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error",
            "python scripts/materialize_production_env.py --overwrite --source-label sops:production --fail-on-error",
        ]
        assert "secret-value-should-not-appear" not in rendered
        assert "sk-or-v1-sensitive-value" not in rendered
        assert str(tmp_path) not in rendered

    def test_api_v1_release_readiness_exposes_safe_refresh_report(self, client, tmp_path, monkeypatch):
        from app.services import release_readiness

        report_dir = tmp_path / "docs" / "verification"
        report_dir.mkdir(parents=True)
        refresh_path = report_dir / "production-readiness-refresh-latest.json"
        refresh_path.write_text(
            json.dumps(
                {
                    "report_type": "production_readiness_refresh",
                    "status": "blocked",
                    "readiness_state": "blocked",
                    "release_candidate_ready": False,
                    "release_allowed": False,
                    "generated_at": "2026-07-09T08:26:55+00:00",
                    "env_file": str(tmp_path / "apps" / "backend" / ".env.prod.example"),
                    "candidate_env_mismatch": {
                        "expected_env_file": "apps/backend/.env.prod",
                        "actual_env_file": str(tmp_path / "apps" / "backend" / ".env.prod.example"),
                    },
                    "blocking_steps": ["candidate_env_file"],
                    "next_action_count": 1,
                    "next_actions": [
                        {
                            "key": "candidate_env_file",
                            "label": "Candidate production env file",
                            "status": "fail",
                            "owner": "release_operator",
                            "requires_secret_update": True,
                            "production_required": True,
                            "source_count": 2,
                            "source_steps": ["release_gate", "production_readiness_summary"],
                            "source_reports": ["production_readiness_summary"],
                            "action": (
                                "Do not refresh from "
                                + str(tmp_path / "apps" / "backend" / ".env.prod.example")
                                + "; rerun with apps/backend/.env.prod"
                            ),
                            "verification_commands": [
                                "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked"
                            ],
                        }
                    ],
                    "refresh_command_plan": [
                        "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod",
                    ],
                    "steps": [
                        {
                            "key": "release_gate",
                            "label": "Release gate",
                            "status": "blocked",
                            "exit_code": 2,
                            "latest_path": str(report_dir / "release-gate-latest.json"),
                            "blocking_steps": ["production_env_validation"],
                            "next_actions": [
                                {"key": "unsafe_internal_action", "action": str(tmp_path)}
                            ],
                        },
                        {
                            "key": "verification_report_hygiene",
                            "label": "Verification report hygiene",
                            "status": "pass",
                            "exit_code": 0,
                            "latest_path": str(report_dir / "verification-report-hygiene-latest.json"),
                            "blocking_steps": [],
                            "next_actions": [],
                        },
                    ],
                    "latest_path": str(refresh_path),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        refresh = body["production_readiness_refresh"]
        rendered = json.dumps(refresh, ensure_ascii=False)
        assert body["evidence_sources"]["production_readiness_refresh_path"] == (
            "docs/verification/production-readiness-refresh-latest.json"
        )
        assert refresh["available"] is True
        assert refresh["status"] == "blocked"
        assert refresh["readiness_state"] == "blocked"
        assert refresh["release_candidate_ready"] is False
        assert refresh["release_allowed"] is False
        assert refresh["path"] == "docs/verification/production-readiness-refresh-latest.json"
        assert refresh["env_file"] == "apps/backend/.env.prod.example"
        assert refresh["candidate_env_mismatch"] == {
            "expected_env_file": "apps/backend/.env.prod",
            "actual_env_file": "apps/backend/.env.prod.example",
        }
        assert refresh["blocking_steps"] == ["candidate_env_file"]
        assert refresh["steps"] == [
            {
                "key": "release_gate",
                "label": "Release gate",
                "status": "blocked",
                "exit_code": 2,
                "latest_path": "docs/verification/release-gate-latest.json",
                "blocking_steps": ["production_env_validation"],
            },
            {
                "key": "verification_report_hygiene",
                "label": "Verification report hygiene",
                "status": "pass",
                "exit_code": 0,
                "latest_path": "docs/verification/verification-report-hygiene-latest.json",
                "blocking_steps": [],
            },
        ]
        assert refresh["next_action_count"] == 1
        assert refresh["operator_command"] == (
            "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked"
        )
        assert refresh["recommended_actions"] == [
            {
                "key": "candidate_env_file",
                "label": "Candidate production env file",
                "status": "fail",
                "owner": "release_operator",
                "requires_secret_update": True,
                "production_required": True,
                "source_count": 2,
                "source_steps": ["release_gate", "production_readiness_summary"],
                "source_reports": ["production_readiness_summary"],
                "action": (
                    "Do not refresh from apps/backend/.env.prod.example; rerun with apps/backend/.env.prod"
                ),
                "verification_commands": [
                    "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked"
                ],
            }
        ]
        assert "next_actions" not in rendered
        assert str(tmp_path) not in rendered

    def test_api_v1_release_readiness_groups_refresh_actions(self, client, tmp_path, monkeypatch):
        from app.services import release_readiness

        report_dir = tmp_path / "docs" / "verification"
        report_dir.mkdir(parents=True)
        refresh_path = report_dir / "production-readiness-refresh-latest.json"
        refresh_path.write_text(
            json.dumps(
                {
                    "report_type": "production_readiness_refresh",
                    "status": "blocked",
                    "readiness_state": "blocked",
                    "release_candidate_ready": False,
                    "release_allowed": False,
                    "generated_at": "2026-07-09T08:40:00+00:00",
                    "env_file": str(tmp_path / "apps" / "backend" / ".env.prod"),
                    "blocking_steps": ["release_gate", "production_readiness_summary"],
                    "next_action_count": 6,
                    "next_actions": [
                        {
                            "key": "env_file",
                            "label": "Production env file",
                            "status": "fail",
                            "category": "configuration",
                            "owner": "deployment_operator",
                            "requires_secret_update": True,
                            "production_required": True,
                            "source_count": 2,
                            "source_steps": ["release_gate", "production_readiness_summary"],
                            "source_reports": ["production_env_validation"],
                            "action": "Materialize " + str(tmp_path / "apps" / "backend" / ".env.prod"),
                            "verification_commands": [
                                "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error"
                            ],
                        },
                        {
                            "key": "production_compose_config",
                            "label": "Missing production env candidate",
                            "status": "fail",
                            "category": "production_compose_config",
                            "owner": "deployment_operator",
                            "requires_secret_update": True,
                            "production_required": True,
                            "source_count": 3,
                            "source_steps": ["release_gate", "production_stack_release"],
                            "source_reports": ["production_compose_config"],
                            "action": "Rerun the production compose config check.",
                            "verification_commands": [
                                "python scripts/check_production_compose_config.py --env-file apps/backend/.env.prod --fail-on-error"
                            ],
                        },
                        {
                            "key": "frontend_release_checks",
                            "label": "Frontend release checks",
                            "status": "fail",
                            "category": "release_verification",
                            "owner": "frontend",
                            "requires_secret_update": False,
                            "production_required": True,
                            "source_count": 1,
                            "source_steps": ["frontend_release_checks"],
                            "source_reports": ["frontend_release_checks"],
                            "action": "Fix frontend npm audit, lint, type-check, and build failures.",
                            "verification_commands": [
                                "python scripts/run_frontend_release_checks.py --fail-on-error"
                            ],
                        },
                        {
                            "key": "env_file",
                            "label": "env_file",
                            "status": "fail",
                            "category": "secret_inventory_validation",
                            "owner": "deployment_operator",
                            "requires_secret_update": True,
                            "production_required": True,
                            "source_count": 1,
                            "source_steps": ["production_readiness_summary"],
                            "source_reports": ["secret_inventory_validation"],
                            "action": "Rerun secret inventory validation.",
                        },
                        {
                            "key": "runtime_profile",
                            "label": "部署 Profile",
                            "status": "warn",
                            "owner": "deployment_operator",
                            "requires_secret_update": False,
                            "production_required": True,
                            "source_count": 2,
                            "source_steps": ["release_gate", "production_readiness_summary"],
                            "source_reports": ["release_gate"],
                            "action": "切换 APP_ENV=production。",
                        },
                        {
                            "key": "secrets",
                            "label": "密钥与认证",
                            "status": "warn",
                            "owner": "deployment_operator",
                            "requires_secret_update": True,
                            "production_required": True,
                            "source_count": 4,
                            "source_steps": ["release_gate", "production_readiness_summary"],
                            "source_reports": ["release_gate"],
                            "action": "将 APP_API_KEY, APP_SECRET_KEY 写入 SOPS/部署密钥。",
                        },
                    ],
                    "refresh_command_plan": [
                        "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod",
                    ],
                    "latest_path": str(refresh_path),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        refresh = r.json()["production_readiness_refresh"]
        rendered = json.dumps(refresh, ensure_ascii=False)
        assert [
            (group["key"], group["label"], group["status"], group["action_count"], group["source_count"])
            for group in refresh["recommended_action_groups"]
        ] == [
            ("release_preparation", "发布准备", "fail", 4, 7),
            ("runtime_and_configuration", "运行配置", "warn", 1, 2),
            ("secrets_and_dependencies", "密钥与依赖", "warn", 1, 4),
        ]
        assert refresh["recommended_action_groups"][0]["source_steps"] == [
            "release_gate",
            "production_readiness_summary",
            "production_stack_release",
            "frontend_release_checks",
        ]
        assert refresh["recommended_action_groups"][0]["has_more_actions"] is False
        assert [item["key"] for item in refresh["recommended_action_groups"][0]["actions"]] == [
            "env_file",
            "production_compose_config",
            "frontend_release_checks",
            "env_file",
        ]
        assert refresh["recommended_action_groups"][0]["actions"][0]["action"] == (
            "Materialize apps/backend/.env.prod"
        )
        assert "next_actions" not in rendered
        assert str(tmp_path) not in rendered

    def test_api_v1_release_readiness_refresh_command_fails_on_blocked(self, client, tmp_path, monkeypatch):
        from app.services import release_readiness

        report_dir = tmp_path / "docs" / "verification"
        report_dir.mkdir(parents=True)
        refresh_path = report_dir / "production-readiness-refresh-latest.json"
        refresh_path.write_text(
            json.dumps(
                {
                    "report_type": "production_readiness_refresh",
                    "status": "blocked",
                    "readiness_state": "blocked",
                    "release_candidate_ready": False,
                    "release_allowed": False,
                    "generated_at": "2026-07-09T08:47:46+00:00",
                    "env_file": str(tmp_path / "apps" / "backend" / ".env.prod"),
                    "blocking_steps": ["release_gate"],
                    "next_action_count": 1,
                    "next_actions": [],
                    "refresh_command_plan": [
                        "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod",
                    ],
                    "latest_path": str(refresh_path),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        refresh = r.json()["production_readiness_refresh"]
        assert refresh["operator_command"] == (
            "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked"
        )

    def test_api_v1_release_readiness_operator_command_prefers_refresh_plan(
        self, client, tmp_path, monkeypatch
    ):
        from app.services import release_readiness

        report_dir = tmp_path / "docs" / "verification"
        report_dir.mkdir(parents=True)
        refresh_path = report_dir / "production-readiness-refresh-latest.json"
        refresh_path.write_text(
            json.dumps(
                {
                    "report_type": "production_readiness_refresh",
                    "status": "blocked",
                    "readiness_state": "blocked",
                    "release_candidate_ready": False,
                    "release_allowed": False,
                    "generated_at": "2026-07-09T08:50:00+00:00",
                    "env_file": str(tmp_path / "apps" / "backend" / ".env.prod"),
                    "blocking_steps": ["release_gate"],
                    "next_action_count": 1,
                    "next_actions": [
                        {
                            "key": "env_file",
                            "label": "Production env file",
                            "status": "fail",
                            "action": "Materialize the production env candidate.",
                            "verification_commands": [
                                "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error",
                                "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
                            ],
                        }
                    ],
                    "refresh_command_plan": [
                        "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod",
                    ],
                    "latest_path": str(refresh_path),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        refresh = r.json()["production_readiness_refresh"]
        assert refresh["operator_command"] == (
            "python scripts/run_production_readiness_refresh.py --env-file apps/backend/.env.prod --fail-on-blocked"
        )

    def test_api_v1_release_readiness_marks_missing_production_digest(self, client, tmp_path, monkeypatch):
        from app.services import release_readiness

        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        digest = r.json()["production_readiness_digest"]
        assert digest == {
            "available": False,
            "path": "docs/verification/production-readiness-digest-latest.json",
            "status": "missing",
            "readiness_state": "unknown",
            "release_allowed": False,
            "blocker_count": None,
            "blocker_total_count": None,
            "blocker_shown_count": 0,
            "top_remediation_count": 0,
            "blockers": [],
            "fresh": False,
            "freshness_status": "missing",
            "age_seconds": None,
            "max_age_seconds": 86400,
            "regenerate_command": "python scripts/summarize_production_readiness.py --report-dir docs/verification",
            "upstream_report_issues": [],
            "candidate_env_mismatch_count": 0,
            "candidate_env_mismatches": [],
        }

    def test_api_v1_release_readiness_includes_main_flow_runtime_smoke(
        self, client, tmp_path, monkeypatch
    ):
        from app.services import release_readiness

        report_dir = tmp_path / "docs" / "verification"
        report_dir.mkdir(parents=True)
        smoke_path = report_dir / "main_flow_runtime_smoke_latest.json"
        smoke_path.write_text(
            json.dumps(
                {
                    "report_type": "main_flow_runtime_smoke",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "session_id": "session-smoke",
                    "marker": "main-flow-smoke-test",
                    "include_committee": False,
                    "steps": {
                        "health": {"ok": True, "status": 200},
                        "create_session": {"ok": True, "status": 200, "session_id": "session-smoke"},
                        "chat": {"ok": True, "status": 200, "duration_ms": 1200},
                        "messages": {"ok": True, "status": 200, "count": 2},
                        "chat_trace": {"ok": True, "status": 200, "count": 5},
                        "briefing": {"ok": True, "status": 200, "duration_ms": 2200},
                        "briefing_trace": {"ok": True, "status": 200, "count": 4},
                        "assessment": {
                            "status": "passed",
                            "errors": [],
                            "warnings": [],
                            "checked_at": datetime.now(timezone.utc).isoformat(),
                            "require_committee": False,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        smoke = body["main_flow_runtime_smoke"]
        assert smoke["available"] is True
        assert smoke["status"] == "passed"
        assert smoke["fresh"] is True
        assert smoke["path"] == "docs/verification/main_flow_runtime_smoke_latest.json"
        assert smoke["session_id"] == "session-smoke"
        assert smoke["step_count"] == 8
        assert smoke["errors"] == []
        assert smoke["warnings"] == []
        assert body["evidence_sources"]["main_flow_runtime_smoke_path"] == (
            "docs/verification/main_flow_runtime_smoke_latest.json"
        )

    def test_api_v1_release_readiness_marks_missing_main_flow_runtime_smoke(
        self, client, tmp_path, monkeypatch
    ):
        from app.services import release_readiness

        monkeypatch.setattr(release_readiness, "_project_root", lambda: tmp_path)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        smoke = r.json()["main_flow_runtime_smoke"]
        assert smoke == {
            "available": False,
            "path": "docs/verification/main_flow_runtime_smoke_latest.json",
            "status": "missing",
            "fresh": False,
            "freshness_status": "missing",
            "age_seconds": None,
            "max_age_seconds": 86400,
            "generated_at": None,
            "checked_at": None,
            "session_id": None,
            "include_committee": False,
            "step_count": 0,
            "steps": [],
            "errors": [],
            "warnings": [],
            "operator_command": (
                "python apps/backend/app/verification/main_flow_runtime_smoke.py "
                "--base-url http://127.0.0.1:8000 --include-committee "
                "--source-id <selected-source-id> --require-real --timeout 120"
            ),
        }

    def test_load_production_readiness_digest_marks_stale_digest(self, tmp_path):
        from app.services.release_readiness import load_production_readiness_digest

        report_path = tmp_path / "production-readiness-digest-latest.json"
        report_path.write_text(
            json.dumps(
                {
                    "report_type": "production_readiness_digest",
                    "status": "blocked",
                    "readiness_state": "blocked",
                    "release_allowed": False,
                    "generated_at": "2026-07-08T00:00:00+00:00",
                    "missing_reports": ["release_gate_secret_inventory"],
                    "stale_reports": ["public_api_leak_scan"],
                    "blocker_count": 1,
                    "top_remediation_count": 1,
                    "blockers": [],
                }
            ),
            encoding="utf-8",
        )

        report = load_production_readiness_digest(
            report_path,
            now=datetime(2026, 7, 9, 2, 0, tzinfo=timezone.utc),
            max_age_seconds=24 * 60 * 60,
        )

        assert report["available"] is True
        assert report["status"] == "blocked"
        assert report["fresh"] is False
        assert report["freshness_status"] == "stale"
        assert report["age_seconds"] == 26 * 60 * 60
        assert report["max_age_seconds"] == 24 * 60 * 60
        assert report["regenerate_command"] == "python scripts/summarize_production_readiness.py --report-dir docs/verification"
        assert report["upstream_report_issues"] == [
            {
                "key": "release_gate_secret_inventory",
                "status": "missing",
                "regenerate_command": (
                    "python scripts/run_release_gate.py --require-env-file "
                    "--env-file apps/backend/.env.prod --backend-url http://127.0.0.1:8000"
                ),
            },
            {
                "key": "public_api_leak_scan",
                "status": "stale",
                "regenerate_command": (
                    "python scripts/scan_public_api_leaks.py "
                    "--backend-url http://127.0.0.1:8000 --fail-on-leak"
                ),
            },
        ]

    def test_release_readiness_observability_passes_when_production_signals_configured(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "APP_ENV", "development")
        monkeypatch.setattr(settings, "LOG_FORMAT", "json")
        monkeypatch.setattr(settings, "SENTRY_DSN", "https://public@example.com/1")
        monkeypatch.setattr(settings, "DOCUMENT_PARSER_ADAPTER", "current")
        monkeypatch.setenv("LOG_FORMAT_SOURCE", "sops:LOG_FORMAT")
        monkeypatch.setenv("SENTRY_DSN_SOURCE", "sops:SENTRY_DSN")

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body_text = r.text
        assert "https://public@example.com/1" not in body_text
        assert "sops:SENTRY_DSN" not in body_text
        assert "sops:LOG_FORMAT" not in body_text
        checks = {item["key"]: item for item in r.json()["checks"]}
        observability = checks["observability"]
        assert observability["status"] == "pass"
        assert observability["remediation"] == ""
        assert observability["signals"]["production_posture_ok"] is True
        assert observability["signals"]["missing_for_production"] == []
        assert observability["signals"]["checks"]["structured_logs"]["status"] == "pass"
        assert observability["signals"]["checks"]["error_tracking"]["status"] == "pass"
        assert observability["signals"]["deployment_sources"]["log_format"]["source"] == "sops"
        assert observability["signals"]["deployment_sources"]["sentry_dsn"]["source"] == "sops"
        assert observability["signals"]["checks"]["structured_logs"]["source"] == "sops"
        assert observability["signals"]["checks"]["error_tracking"]["source"] == "sops"

    def test_api_v1_release_readiness_fails_unsafe_production_profile(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_DEBUG", True)
        monkeypatch.setattr(settings, "APP_API_KEY", "")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "dev-secret-key")
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
        monkeypatch.setattr(settings, "REDIS_ENABLED", False)
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", "memory://")
        monkeypatch.setattr(settings, "REQUIRE_REDIS_IN_PRODUCTION", True)
        monkeypatch.setattr(settings, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", True)
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "minioadmin")
        monkeypatch.setattr(settings, "DOCUMENT_PARSER_ADAPTER", "current")

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "fail"
        assert body["deployment_profile"]["mode"] == "production_required"
        checks = {item["key"]: item for item in body["checks"]}
        assert checks["runtime_profile"]["status"] == "fail"
        assert checks["secrets"]["status"] == "fail"
        assert checks["shared_redis"]["status"] == "fail"
        assert checks["object_storage"]["status"] == "fail"
        assert checks["rate_limit_storage"]["status"] == "fail"
        assert "APP_DEBUG" in checks["runtime_profile"]["remediation"]
        assert "production_config_errors" in checks["runtime_profile"]["remediation"]
        assert "APP_API_KEY" in checks["secrets"]["remediation"]
        assert "APP_SECRET_KEY" in checks["secrets"]["remediation"]
        assert "OPENROUTER_API_KEY" in checks["secrets"]["remediation"]
        assert body["summary"]["fail"] >= 5

    def test_api_v1_release_readiness_includes_live_readiness_failures(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health

        async def _fake_ready():
            return JSONResponse(
                {
                    "status": "unhealthy",
                    "checks": {
                        "database": {"ok": False, "error": "db down"},
                        "openrouter": {"ok": True},
                    },
                    "deployment_profile": {
                        "mode": "local_mvp",
                        "required_checks": ["database", "openrouter"],
                        "blocking_checks": ["database"],
                    },
                },
                status_code=503,
            )

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        assert body["status"] == "fail"
        assert checks["runtime_readiness"]["status"] == "fail"
        assert checks["runtime_readiness"]["source_endpoint"] == "/api/v1/health/ready"
        assert checks["runtime_readiness"]["signals"]["blocking_checks"] == ["database"]
        assert "/api/v1/health/ready" in checks["runtime_readiness"]["remediation"]
        assert "database" in checks["runtime_readiness"]["remediation"]

    def test_release_readiness_runtime_pass_has_no_remediation(self):
        from app.services.release_readiness import _runtime_readiness_check

        check = _runtime_readiness_check(
            {
                "status": "ready",
                "deployment_profile": {"blocking_checks": []},
                "checks": {
                    "database": {"ok": True},
                    "redis": {"ok": True},
                    "minio": {"ok": True},
                    "openrouter": {"ok": True},
                },
            }
        )

        assert check["status"] == "pass"
        assert check["remediation"] == ""

    def test_production_secrets_tls_checklist_reports_sources_without_secret_values(
        self,
        monkeypatch,
    ):
        from app.config import settings
        from app.services.release_readiness import build_production_secrets_tls_checklist

        monkeypatch.setattr(settings, "APP_API_KEY", "")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "dev-secret-key")
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECURE", False)
        monkeypatch.setattr(settings, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", True)
        monkeypatch.setattr(settings, "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION", False)
        monkeypatch.setenv("OPENROUTER_KEY", "test-openrouter-key")

        report = build_production_secrets_tls_checklist(settings)

        assert report["report_type"] == "production_secrets_tls_checklist"
        assert report["status"] == "fail"
        secret_items = {item["key"]: item for item in report["secret_items"]}
        assert secret_items["APP_API_KEY"]["status"] == "fail"
        assert secret_items["APP_SECRET_KEY"]["status"] == "fail"
        assert secret_items["OPENROUTER_API_KEY"]["status"] == "pass"
        assert secret_items["OPENROUTER_API_KEY"]["source"] == "sops"
        assert secret_items["MINIO_ACCESS_KEY"]["status"] == "fail"
        assert secret_items["MINIO_SECRET_KEY"]["status"] == "fail"
        tls_controls = {item["key"]: item for item in report["tls_controls"]}
        assert tls_controls["MINIO_SECURE"]["status"] == "fail"
        assert tls_controls["ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION"]["status"] == "pass"
        rendered = str(report)
        assert "test-openrouter-key" not in rendered
        assert "minioadmin" not in rendered
        assert "OPENROUTER_KEY" not in rendered

    def test_production_secrets_tls_checklist_fails_untrusted_secret_source(
        self,
        monkeypatch,
    ):
        from app.config import settings
        from app.services.release_readiness import build_production_secrets_tls_checklist

        monkeypatch.setattr(settings, "APP_API_KEY", "app-api-key-that-is-long-enough")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "MINIO_SECURE", True)
        monkeypatch.setattr(settings, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", False)
        monkeypatch.setenv("APP_API_KEY_SOURCE", "plain-env:manual-copy")
        monkeypatch.setenv("APP_SECRET_KEY_SOURCE", "sops:APP_SECRET_KEY")
        monkeypatch.setenv("OPENROUTER_API_KEY_SOURCE", "sops:OPENROUTER_API_KEY")

        report = build_production_secrets_tls_checklist(settings)

        secret_items = {item["key"]: item for item in report["secret_items"]}
        assert report["status"] == "fail"
        assert secret_items["APP_API_KEY"]["status"] == "fail"
        assert secret_items["APP_API_KEY"]["source_provenance_ok"] is False
        assert secret_items["APP_API_KEY"]["source_marker_family"] == "untrusted"
        assert "plain-env:manual-copy" not in str(report)
        assert "app-api-key-that-is-long-enough" not in str(report)

    def test_api_v1_production_secrets_tls_exposes_checklist(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "APP_API_KEY", "")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "dev-secret-key")
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "fake-openrouter-key")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECURE", False)
        monkeypatch.setattr(settings, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", True)

        r = client.get("/api/v1/health/production-secrets-tls")

        assert r.status_code == 200
        body = r.json()
        assert body["report_type"] == "production_secrets_tls_checklist"
        assert body["status"] == "fail"
        assert body["summary"]["fail"] >= 4
        assert any(item["key"] == "MINIO_SECURE" for item in body["tls_controls"])

    def test_public_operational_report_sanitizer_removes_internal_paths_aliases_and_secret_values(self):
        from app.services.release_readiness import sanitize_public_operational_report

        raw = {
            "path": "C:\\example\\research-os\\docs\\verification\\production-preflight-latest.json",
            "fallback_source": "sops:OPENROUTER_KEY",
            "endpoint_source": "env:MINIO_ENDPOINT",
            "default_credential": "minioadmin",
            "remediation": "APP_SECRET_KEY 至少 32 字符且不能使用 dev-secret-key。",
            "redis_url": _test_redis_uri(1),
            "items": [
                "C:/example/research-os/logs/backend.log",
                "sops:MINIO_SECRET_KEY",
                "APP_SECRET_KEY",
            ],
        }

        sanitized = sanitize_public_operational_report(raw)
        rendered = str(sanitized)

        assert sanitized["path"] == "docs/verification/production-preflight-latest.json"
        assert sanitized["fallback_source"] == "sops"
        assert sanitized["endpoint_source"] == "configured"
        assert sanitized["default_credential"] == "[redacted-secret]"
        assert sanitized["remediation"] == "APP_SECRET_KEY 至少 32 字符且不能使用 [redacted-secret]。"
        assert sanitized["redis_url"] == "redis://<credentials>@redis:6379/1"
        assert sanitized["items"][0] == "backend.log"
        assert sanitized["items"][1] == "sops"
        assert sanitized["items"][2] == "APP_SECRET_KEY"
        assert "E:\\Works" not in rendered
        assert "E:/Works" not in rendered
        assert "OPENROUTER_KEY" not in rendered
        assert "redis-pass" not in rendered
        assert "minioadmin" not in rendered
        assert "dev-secret-key" not in rendered

    def test_api_v1_release_readiness_sanitizes_report_at_response_boundary(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        def _unsafe_release_report(*args, **kwargs):
            return {
                "status": "warn",
                "checks": [
                    {
                        "key": "unsafe",
                        "label": "unsafe",
                        "status": "warn",
                        "signals": {
                            "source": "sops:OPENROUTER_KEY",
                            "path": "C:\\example\\research-os\\logs\\backend.log",
                            "default_credential": "minioadmin",
                            "redis_url": _test_redis_uri(1),
                        },
                    }
                ],
            }

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(health, "build_release_readiness_report", _unsafe_release_report)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        rendered = str(body)
        signals = body["checks"][0]["signals"]
        assert signals["source"] == "sops"
        assert signals["path"] == "backend.log"
        assert signals["default_credential"] == "[redacted-secret]"
        assert signals["redis_url"] == "redis://<credentials>@redis:6379/1"
        assert "OPENROUTER_KEY" not in rendered
        assert "E:\\Works" not in rendered
        assert "redis-pass" not in rendered
        assert "minioadmin" not in rendered

    def test_release_readiness_sanitizes_secret_provenance_sources(self, db, monkeypatch):
        from app.config import settings
        from app.services import release_readiness

        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "fake-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:6379/1")
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "127.0.0.1:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.delenv("OPENROUTER_API_KEY_SOURCE", raising=False)
        monkeypatch.setenv("OPENROUTER_KEY", "fake-openrouter-key")
        monkeypatch.setenv("REDIS_URL_SOURCE", "sops:REDIS_URL")
        monkeypatch.setenv("MINIO_ACCESS_KEY_SOURCE", "sops:MINIO_ACCESS_KEY")
        monkeypatch.setenv("MINIO_SECRET_KEY_SOURCE", "sops:MINIO_SECRET_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {
                "ok": True,
                "backend": "minio",
                "provider": "minio",
                "secure": False,
                "operation": "write_read_delete",
            },
        )

        report = release_readiness.build_release_readiness_report(
            cfg=settings,
            runtime_readiness={"status": "ready", "checks": {}, "deployment_profile": {"blocking_checks": []}},
            db=db,
        )

        checks = {item["key"]: item for item in report["checks"]}
        provenance = checks["secrets"]["signals"]["secret_provenance"]
        assert provenance["openrouter_api_key"]["source"] == "sops"
        assert provenance["redis_url"]["source"] == "sops"
        assert provenance["minio_access_key"]["source"] == "sops"
        rendered = str(report)
        assert "OPENROUTER_KEY" not in rendered
        assert "fake-openrouter-key" not in rendered
        assert "prod-secret-key" not in rendered

    def test_release_readiness_uses_global_secret_source_when_key_sources_are_absent(self, db, monkeypatch):
        from app.config import settings
        from app.services import release_readiness

        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_API_KEY", "test-app-api-key")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "REDIS_URL", _test_redis_uri(0))
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", _test_redis_uri(1))
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files-prod")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        for key in (
            "APP_API_KEY_SOURCE",
            "APP_SECRET_KEY_SOURCE",
            "OPENROUTER_API_KEY_SOURCE",
            "REDIS_URL_SOURCE",
            "RATE_LIMIT_STORAGE_URI_SOURCE",
            "MINIO_ENDPOINT_SOURCE",
            "MINIO_BUCKET_SOURCE",
            "MINIO_ACCESS_KEY_SOURCE",
            "MINIO_SECRET_KEY_SOURCE",
            "OPENROUTER_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("SECRETS_SOURCE", "sops:" + "production")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {"ok": True, "operation": "write_read_delete", "backend": "minio"},
        )

        report = release_readiness.build_release_readiness_report(
            cfg=settings,
            runtime_readiness={"status": "ready", "checks": {}, "deployment_profile": {"blocking_checks": []}},
            db=db,
        )

        checks = {item["key"]: item for item in report["checks"]}
        provenance = checks["secrets"]["signals"]["secret_provenance"]
        for key in (
            "app_api_key",
            "app_secret_key",
            "openrouter_api_key",
            "redis_url",
            "rate_limit_storage_uri",
            "minio_endpoint",
            "minio_bucket",
            "minio_access_key",
            "minio_secret_key",
        ):
            assert provenance[key]["source"] == "sops"
        rendered = str(report)
        operator_commands = report["production_env_materialization"]["operator_commands"]
        assert operator_commands == [
            release_readiness.PRODUCTION_ENV_MATERIALIZATION_DRY_RUN_COMMAND,
            release_readiness.PRODUCTION_ENV_MATERIALIZATION_WRITE_COMMAND,
        ]
        approved_materializer_commands = [
            *operator_commands,
        ]
        rendered_without_operator_commands = rendered
        for operator_command in approved_materializer_commands:
            assert operator_command in rendered_without_operator_commands
            rendered_without_operator_commands = rendered_without_operator_commands.replace(
                operator_command,
                "",
            )
        assert "sops:" + "production" not in rendered_without_operator_commands
        assert "test-openrouter-key" not in rendered
        assert "redis-pass" not in rendered
        assert "prod-secret-key" not in rendered

    def test_release_readiness_fails_production_secrets_with_untrusted_source(self, db, monkeypatch):
        from app.config import settings
        from app.services import release_readiness

        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_API_KEY", "test-app-api-key")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "REDIS_URL", _test_redis_uri(0))
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", _test_redis_uri(1))
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files-prod")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setenv("APP_API_KEY_SOURCE", "plain-env:manual-copy")
        monkeypatch.setenv("APP_SECRET_KEY_SOURCE", "sops:APP_SECRET_KEY")
        monkeypatch.setenv("OPENROUTER_API_KEY_SOURCE", "sops:OPENROUTER_API_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {"ok": True, "operation": "write_read_delete", "backend": "minio"},
        )

        report = release_readiness.build_release_readiness_report(
            cfg=settings,
            runtime_readiness={"status": "ready", "checks": {}, "deployment_profile": {"blocking_checks": []}},
            db=db,
        )

        checks = {item["key"]: item for item in report["checks"]}
        secrets = checks["secrets"]
        provenance = secrets["signals"]["secret_provenance"]
        assert secrets["status"] == "fail"
        assert secrets["signals"]["secret_source_provenance_ok"] is False
        assert provenance["app_api_key"]["source_provenance_ok"] is False
        assert provenance["app_api_key"]["source_marker_family"] == "untrusted"
        assert "plain-env:manual-copy" not in str(report)
        assert "test-openrouter-key" not in str(report)

    def test_release_readiness_flags_untrusted_observability_sources(self, db, monkeypatch):
        from app.config import settings
        from app.services import release_readiness

        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_API_KEY", "test-app-api-key")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "REDIS_URL", "redis://redis:6379/0")
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", "redis://redis:6379/1")
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files-prod")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setattr(settings, "MINIO_SECURE", True)
        monkeypatch.setattr(settings, "LOG_FORMAT", "json")
        sentry_dsn = _test_sentry_dsn()
        monkeypatch.setattr(settings, "SENTRY_DSN", sentry_dsn)
        monkeypatch.setattr(settings, "APP_AUTH_WHITELIST", ["/api/v1/health", "/metrics"])
        monkeypatch.setenv("APP_API_KEY_SOURCE", "sops:APP_API_KEY")
        monkeypatch.setenv("APP_SECRET_KEY_SOURCE", "sops:APP_SECRET_KEY")
        monkeypatch.setenv("OPENROUTER_API_KEY_SOURCE", "sops:OPENROUTER_API_KEY")
        monkeypatch.setenv("LOG_FORMAT_SOURCE", "plain-env:manual-copy")
        monkeypatch.setenv("SENTRY_DSN_SOURCE", "sops:SENTRY_DSN")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {"ok": True, "operation": "write_read_delete", "backend": "minio"},
        )

        report = release_readiness.build_release_readiness_report(
            cfg=settings,
            runtime_readiness={"status": "ready", "checks": {}, "deployment_profile": {"blocking_checks": []}},
            db=db,
        )

        checks = {item["key"]: item for item in report["checks"]}
        observability = checks["observability"]
        structured_logs = observability["signals"]["checks"]["structured_logs"]
        assert observability["status"] == "warn"
        assert observability["signals"]["production_posture_ok"] is False
        assert structured_logs["status"] == "warn"
        assert structured_logs["source_marker_family"] == "untrusted"
        assert structured_logs["source_provenance_ok"] is False
        assert "plain-env:manual-copy" not in str(report)
        assert sentry_dsn not in str(report)

    def test_release_readiness_flags_untrusted_shared_state_sources(self, db, monkeypatch):
        from app.config import settings
        from app.services import release_readiness

        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_API_KEY", "test-app-api-key")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "REDIS_URL", _test_redis_uri(0))
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", _test_redis_uri(1))
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files-prod")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setattr(settings, "MINIO_SECURE", True)
        monkeypatch.setenv("APP_API_KEY_SOURCE", "sops:APP_API_KEY")
        monkeypatch.setenv("APP_SECRET_KEY_SOURCE", "sops:APP_SECRET_KEY")
        monkeypatch.setenv("OPENROUTER_API_KEY_SOURCE", "sops:OPENROUTER_API_KEY")
        monkeypatch.setenv("REDIS_URL_SOURCE", "plain-env:manual-copy")
        monkeypatch.setenv("RATE_LIMIT_STORAGE_URI_SOURCE", "plain-env:manual-copy")
        monkeypatch.setenv("MINIO_ENDPOINT_SOURCE", "env:MINIO_ENDPOINT")
        monkeypatch.setenv("MINIO_BUCKET_SOURCE", "env:MINIO_BUCKET")
        monkeypatch.setenv("MINIO_ACCESS_KEY_SOURCE", "sops:MINIO_ACCESS_KEY")
        monkeypatch.setenv("MINIO_SECRET_KEY_SOURCE", "sops:MINIO_SECRET_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {"ok": True, "operation": "write_read_delete", "backend": "minio"},
        )

        report = release_readiness.build_release_readiness_report(
            cfg=settings,
            runtime_readiness={"status": "ready", "checks": {}, "deployment_profile": {"blocking_checks": []}},
            db=db,
        )

        checks = {item["key"]: item for item in report["checks"]}
        shared_redis = checks["shared_redis"]
        rate_limit_storage = checks["rate_limit_storage"]
        assert shared_redis["status"] == "fail"
        assert shared_redis["signals"]["redis_url_source_provenance_ok"] is False
        assert shared_redis["signals"]["redis_url_source_marker_family"] == "untrusted"
        assert rate_limit_storage["status"] == "fail"
        assert rate_limit_storage["signals"]["storage_uri_source_provenance_ok"] is False
        assert rate_limit_storage["signals"]["storage_uri_source_marker_family"] == "untrusted"
        rendered = str(report)
        assert "plain-env:manual-copy" not in rendered
        assert "redis-pass" not in rendered
        assert "test-openrouter-key" not in rendered

    def test_release_readiness_flags_untrusted_object_storage_secret_sources(self, db, monkeypatch):
        from app.config import settings
        from app.services import release_readiness

        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_API_KEY", "test-app-api-key")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "REDIS_URL", _test_redis_uri(0))
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", _test_redis_uri(1))
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files-prod")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setattr(settings, "MINIO_SECURE", True)
        monkeypatch.setenv("APP_API_KEY_SOURCE", "sops:APP_API_KEY")
        monkeypatch.setenv("APP_SECRET_KEY_SOURCE", "sops:APP_SECRET_KEY")
        monkeypatch.setenv("OPENROUTER_API_KEY_SOURCE", "sops:OPENROUTER_API_KEY")
        monkeypatch.setenv("REDIS_URL_SOURCE", "sops:REDIS_URL")
        monkeypatch.setenv("RATE_LIMIT_STORAGE_URI_SOURCE", "sops:RATE_LIMIT_STORAGE_URI")
        monkeypatch.setenv("MINIO_ENDPOINT_SOURCE", "sops:MINIO_ENDPOINT")
        monkeypatch.setenv("MINIO_BUCKET_SOURCE", "sops:MINIO_BUCKET")
        monkeypatch.setenv("MINIO_ACCESS_KEY_SOURCE", "plain-env:manual-copy")
        monkeypatch.setenv("MINIO_SECRET_KEY_SOURCE", "sops:MINIO_SECRET_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {"ok": True, "operation": "write_read_delete", "backend": "minio"},
        )

        report = release_readiness.build_release_readiness_report(
            cfg=settings,
            runtime_readiness={"status": "ready", "checks": {}, "deployment_profile": {"blocking_checks": []}},
            db=db,
        )

        checks = {item["key"]: item for item in report["checks"]}
        object_storage = checks["object_storage"]
        assert object_storage["status"] == "fail"
        assert object_storage["signals"]["production_posture_ok"] is False
        assert object_storage["signals"]["secret_source_provenance_ok"] is False
        assert object_storage["signals"]["secret_source_failures"] == ["MINIO_ACCESS_KEY"]
        sources = object_storage["signals"]["source_provenance"]
        assert sources["minio_access_key"]["source_marker_family"] == "untrusted"
        assert sources["minio_access_key"]["source_provenance_ok"] is False
        rendered = str(report)
        assert "plain-env:manual-copy" not in rendered
        assert "prod-access-key" not in rendered
        assert "prod-secret-key" not in rendered

    def test_production_preflight_report_summarizes_acceptance_and_next_actions(self):
        from app.services.release_readiness import build_production_preflight_report

        release_report = {
            "status": "warn",
            "summary": {"pass": 5, "warn": 2, "fail": 0},
            "deployment_profile": {"mode": "local_mvp"},
            "checks": [
                {
                    "key": "runtime_readiness",
                    "label": "实时依赖",
                    "status": "pass",
                    "detail": "所有实时依赖可用",
                    "remediation": "",
                    "signals": {"status": "ready"},
                },
                {
                    "key": "secrets",
                    "label": "密钥与认证",
                    "status": "warn",
                    "detail": "存在本地空密钥或默认密钥",
                    "remediation": "将 APP_API_KEY 写入 SOPS/部署密钥并在启动脚本中注入。",
                    "signals": {"secret_provenance": {"app_api_key": {"source": "default_or_missing"}}},
                },
                {
                    "key": "object_storage",
                    "label": "对象存储",
                    "status": "warn",
                    "detail": "对象存储读写探针通过，但仍使用本地默认配置",
                    "remediation": "生产环境请将 MINIO_ACCESS_KEY/MINIO_SECRET_KEY 改为非默认凭据，并设置 MINIO_SECURE=true。",
                    "signals": {"probe_ok": True, "default_credentials": True},
                },
                {
                    "key": "rate_limit_storage",
                    "label": "rate limit storage",
                    "status": "warn",
                    "detail": "rate limit state is using local memory storage",
                    "remediation": "",
                    "signals": {
                        "storage_uri": {"scheme": "memory", "set": True},
                        "shared_storage": False,
                    },
                },
            ],
        }
        runtime_readiness = {
            "status": "ready",
            "checks": {
                "database": {"ok": True},
                "redis": {"ok": True},
                "minio": {"ok": True},
                "openrouter": {"ok": True},
            },
            "deployment_profile": {"blocking_checks": []},
        }

        report = build_production_preflight_report(
            release_readiness=release_report,
            runtime_readiness=runtime_readiness,
            local_dependency_report={
                "path": "C:\\example\\research-os\\docs\\verification\\local-dependencies-latest.json",
                "status": "passed",
                "checks": {"redis": "healthy", "minio": "healthy"},
            },
            public_api_leak_scan={
                "available": True,
                "path": "C:\\example\\research-os\\docs\\verification\\public-api-leak-scan-latest.json",
                "status": "pass",
                "fresh": True,
                "freshness_status": "fresh",
                "age_seconds": 120,
                "max_age_seconds": 86400,
                "leak_count": 0,
                "endpoint_count": 6,
                "summary": {"pass": 6, "warn": 0, "fail": 0},
            },
            production_dry_run={
                "report_type": "production_profile_dry_run",
                "status": "pass",
                "production_config_errors": [],
                "runtime_dependency_blockers": [],
            },
        )

        assert report["report_type"] == "production_preflight"
        assert report["status"] == "warn"
        assert report["acceptance"]["local_demo_ready"] is True
        assert report["acceptance"]["production_ready"] is False
        assert report["blockers"] == []
        assert [item["key"] for item in report["warnings"]] == [
            "secrets",
            "object_storage",
            "rate_limit_storage",
            "main_flow_runtime_smoke",
        ]
        assert report["next_actions"][0]["key"] == "secrets"
        assert "SOPS" in report["next_actions"][0]["action"]
        assert report["next_actions"][0]["owner"] == "deployment_operator"
        assert report["next_actions"][0]["can_auto_fix"] is False
        assert report["next_actions"][0]["requires_secret_update"] is True
        assert report["next_actions"][0]["production_required"] is True
        assert report["next_actions"][0]["local_demo_blocking"] is False
        assert report["next_actions"][0]["config_keys"] == ["APP_API_KEY", "APP_SECRET_KEY", "OPENROUTER_API_KEY"]
        assert report["next_actions"][0]["verification"][0]["target"] == "/api/v1/health/production-secrets-tls"
        assert report["next_actions"][0]["verification"][0]["expectation"] == "status=pass"
        rate_limit_action = next(item for item in report["next_actions"] if item["key"] == "rate_limit_storage")
        assert rate_limit_action["action"] != "...。"
        assert "RATE_LIMIT_STORAGE_URI" in rate_limit_action["action"]
        assert "Redis" in rate_limit_action["action"]
        assert rate_limit_action["config_keys"] == ["RATE_LIMIT_STORAGE_URI", "REDIS_ENABLED"]
        assert rate_limit_action["requires_secret_update"] is False
        assert report["evidence_sources"]["ready_endpoint"] == "/api/v1/health/ready"
        assert report["evidence_sources"]["release_readiness_endpoint"] == "/api/v1/health/release-readiness"
        assert report["evidence_sources"]["local_dependency_report_path"] == "docs/verification/local-dependencies-latest.json"
        assert "minioadmin" not in str(report)
        assert "E:\\Works" not in str(report)
        assert report["production_secrets_tls"]["report_type"] == "production_secrets_tls_checklist"
        assert report["public_api_leak_scan"]["status"] == "pass"
        assert report["public_api_leak_scan"]["leak_count"] == 0
        assert report["evidence_sources"]["public_api_leak_scan_report_path"] == "docs/verification/public-api-leak-scan-latest.json"
        assert report["decision_paths"]["local_demo"]["status"] == "ready"
        assert report["decision_paths"]["local_demo"]["ready"] is True
        assert report["decision_paths"]["local_demo"]["required_action_count"] == 0
        assert report["decision_paths"]["local_demo"]["production_warning_count"] == 4
        assert report["decision_paths"]["production_release"]["status"] == "needs_action"
        assert report["decision_paths"]["production_release"]["ready"] is False
        assert report["decision_paths"]["production_release"]["required_action_count"] == 4
        assert report["decision_paths"]["production_release"]["action_keys"] == [
            "secrets",
            "object_storage",
            "rate_limit_storage",
            "main_flow_runtime_smoke",
        ]

    def test_production_preflight_allows_local_demo_when_smoke_passes_despite_degraded_optional_deps(
        self,
    ):
        from app.services.release_readiness import build_production_preflight_report

        report = build_production_preflight_report(
            release_readiness={
                "status": "warn",
                "summary": {"pass": 2, "warn": 1, "fail": 0},
                "deployment_profile": {"mode": "local_mvp"},
                "main_flow_runtime_smoke": {
                    "available": True,
                    "status": "passed",
                    "fresh": True,
                    "errors": [],
                    "warnings": [],
                    "path": "docs/verification/main_flow_runtime_smoke_latest.json",
                },
                "checks": [
                    {
                        "key": "runtime_readiness",
                        "label": "实时依赖",
                        "status": "warn",
                        "detail": "实时依赖存在降级项",
                        "remediation": "可选 Redis 降级",
                        "signals": {"status": "degraded", "blocking_checks": [], "failed_checks": ["redis"]},
                    }
                ],
            },
            runtime_readiness={
                "status": "degraded",
                "checks": {
                    "database": {"ok": True},
                    "openrouter": {"ok": True},
                    "redis": {"ok": False},
                },
                "deployment_profile": {"mode": "local_mvp", "blocking_checks": []},
            },
            local_dependency_report={"status": "warn", "checks": {"redis": "missing"}},
            public_api_leak_scan={
                "available": True,
                "status": "pass",
                "fresh": True,
                "freshness_status": "fresh",
                "leak_count": 0,
            },
            production_dry_run={
                "report_type": "production_profile_dry_run",
                "status": "fail",
                "production_config_errors": ["APP_ENV must be production"],
                "runtime_dependency_blockers": ["redis"],
            },
        )

        assert report["acceptance"]["runtime_ready"] is False
        assert report["acceptance"]["local_demo_ready"] is True
        assert report["acceptance"]["main_flow_runtime_smoke_status"] == "passed"
        assert report["decision_paths"]["local_demo"]["status"] == "ready"
        assert report["decision_paths"]["local_demo"]["required_action_count"] == 0
        assert report["decision_paths"]["production_release"]["ready"] is False
        assert report["summary"]["main_flow_runtime_smoke_status"] == "passed"

    def test_production_preflight_blocks_when_production_dry_run_fails(self):
        from app.services.release_readiness import build_production_preflight_report

        report = build_production_preflight_report(
            release_readiness={
                "status": "pass",
                "summary": {"pass": 8, "warn": 0, "fail": 0},
                "deployment_profile": {"mode": "production_required"},
                "checks": [],
            },
            runtime_readiness={
                "status": "ready",
                "checks": {
                    "database": {"ok": True},
                    "openrouter": {"ok": True},
                    "redis": {"ok": True},
                    "minio": {"ok": True},
                },
                "deployment_profile": {"blocking_checks": []},
            },
            local_dependency_report={"status": "passed", "checks": {}},
            public_api_leak_scan={
                "available": True,
                "status": "pass",
                "fresh": True,
                "freshness_status": "fresh",
                "leak_count": 0,
                "endpoint_count": 6,
                "summary": {"pass": 6, "warn": 0, "fail": 0},
            },
            production_dry_run={
                "report_type": "production_profile_dry_run",
                "status": "fail",
                "production_config_errors": ["DEPLOYMENT_CONFIG_ID must be set in production"],
                "runtime_dependency_blockers": [],
            },
        )

        assert report["status"] == "warn"
        assert report["acceptance"]["production_ready"] is False
        assert report["summary"]["production_dry_run_status"] == "fail"
        assert [item["key"] for item in report["next_actions"]] == [
            "production_dry_run",
            "main_flow_runtime_smoke",
        ]
        assert report["next_actions"][0]["category"] == "production_profile"
        assert report["decision_paths"]["production_release"]["status"] == "needs_action"
        assert report["decision_paths"]["production_release"]["required_action_count"] == 2

    def test_api_v1_production_preflight_exposes_exportable_report(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings

        async def _fake_ready():
            return JSONResponse(
                {
                    "status": "ready",
                    "checks": {
                        "database": {"ok": True},
                        "redis": {"ok": True},
                        "minio": {"ok": True},
                        "openrouter": {"ok": True},
                    },
                    "deployment_profile": {"mode": "local_mvp", "blocking_checks": []},
                }
            )

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "DEPLOYMENT_CONFIG_ID", "prod-runtime-20260708T081500Z")

        r = client.get("/api/v1/health/production-preflight")

        assert r.status_code == 200
        body = r.json()
        assert body["report_type"] == "production_preflight"
        assert body["evidence_sources"]["ready_endpoint"] == "/api/v1/health/ready"
        assert body["evidence_sources"]["release_readiness_endpoint"] == "/api/v1/health/release-readiness"
        assert "acceptance" in body
        assert "next_actions" in body
        assert body["production_dry_run"]["report_type"] == "production_profile_dry_run"
        assert body["production_dry_run"]["mutates_active_settings"] is False
        assert body["runtime_config_identity"] == {
            "deployment_config_id": "prod-runtime-20260708T081500Z",
            "set": True,
        }
        assert body["production_secrets_tls"]["report_type"] == "production_secrets_tls_checklist"
        assert "public_api_leak_scan" in body
        assert "public_api_leak_scan_report_path" in body["evidence_sources"]
        assert "decision_paths" in body
        assert "local_demo" in body["decision_paths"]
        assert "production_release" in body["decision_paths"]

    def test_load_public_api_leak_scan_report_sanitizes_path_and_payload(self, tmp_path):
        from app.services.release_readiness import load_public_api_leak_scan_report

        report_path = tmp_path / "public-api-leak-scan-latest.json"
        report_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "generated_at": "2026-07-08T00:00:00Z",
                    "leak_count": 0,
                    "endpoint_count": 6,
                    "summary": {"pass": 6, "warn": 0, "fail": 0},
                    "endpoints": [
                        {
                            "path": "/api/v1/health",
                            "status": "pass",
                            "leaks": [],
                            "raw_path": "C:\\example\\research-os\\secrets\\prod.env",
                        }
                    ],
                    "sensitive_sample": "sops:OPENROUTER_KEY",
                }
            ),
            encoding="utf-8",
        )

        report = load_public_api_leak_scan_report(
            report_path,
            now=datetime(2026, 7, 8, 0, 10, tzinfo=timezone.utc),
        )

        assert report["available"] is True
        assert report["status"] == "pass"
        assert report["fresh"] is True
        assert report["freshness_status"] == "fresh"
        assert report["leak_count"] == 0
        assert report["endpoint_count"] == 6
        assert report["summary"] == {"pass": 6, "warn": 0, "fail": 0}
        assert "endpoints" not in report
        rendered = str(report)
        assert "E:\\Works" not in rendered
        assert "OPENROUTER_KEY" not in rendered
        assert "sops:" not in rendered

    def test_load_public_api_leak_scan_report_marks_stale_scan(self, tmp_path):
        from app.services.release_readiness import load_public_api_leak_scan_report

        report_path = tmp_path / "public-api-leak-scan-latest.json"
        report_path.write_text(
            json.dumps(
                {
                    "status": "pass",
                    "generated_at": "2026-07-06T00:00:00Z",
                    "leak_count": 0,
                    "endpoint_count": 6,
                    "summary": {"pass": 6, "warn": 0, "fail": 0},
                }
            ),
            encoding="utf-8",
        )

        report = load_public_api_leak_scan_report(
            report_path,
            now=datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc),
            max_age_seconds=24 * 60 * 60,
        )

        assert report["status"] == "pass"
        assert report["fresh"] is False
        assert report["freshness_status"] == "stale"
        assert report["age_seconds"] == 172800
        assert report["max_age_seconds"] == 86400

    def test_production_preflight_blocks_failed_public_api_leak_scan(self):
        from app.services.release_readiness import build_production_preflight_report

        report = build_production_preflight_report(
            release_readiness={
                "status": "pass",
                "summary": {"pass": 8, "warn": 0, "fail": 0},
                "deployment_profile": {"mode": "production"},
                "checks": [],
            },
            runtime_readiness={
                "status": "ready",
                "checks": {"database": {"ok": True}, "openrouter": {"ok": True}},
                "deployment_profile": {"blocking_checks": []},
            },
            local_dependency_report={"status": "passed", "checks": {}},
            public_api_leak_scan={
                "available": True,
                "status": "fail",
                "leak_count": 1,
                "endpoint_count": 6,
                "summary": {"pass": 5, "warn": 0, "fail": 1},
            },
        )

        assert report["status"] == "fail"
        assert report["summary"]["blocker_count"] == 1
        assert report["blockers"][0]["key"] == "public_api_leak_scan"
        assert report["next_actions"][0]["key"] == "public_api_leak_scan"

    def test_production_preflight_warns_on_stale_public_api_leak_scan(self):
        from app.services.release_readiness import build_production_preflight_report

        report = build_production_preflight_report(
            release_readiness={
                "status": "pass",
                "summary": {"pass": 8, "warn": 0, "fail": 0},
                "deployment_profile": {"mode": "production"},
                "checks": [],
            },
            runtime_readiness={
                "status": "ready",
                "checks": {"database": {"ok": True}, "openrouter": {"ok": True}},
                "deployment_profile": {"blocking_checks": []},
            },
            local_dependency_report={"status": "passed", "checks": {}},
            public_api_leak_scan={
                "available": True,
                "status": "pass",
                "fresh": False,
                "freshness_status": "stale",
                "age_seconds": 172800,
                "max_age_seconds": 86400,
                "leak_count": 0,
                "endpoint_count": 6,
                "summary": {"pass": 6, "warn": 0, "fail": 0},
            },
            production_dry_run={
                "report_type": "production_profile_dry_run",
                "status": "pass",
                "production_config_errors": [],
                "runtime_dependency_blockers": [],
            },
        )

        assert report["status"] == "warn"
        assert report["acceptance"]["local_demo_ready"] is True
        assert report["acceptance"]["production_ready"] is False
        assert report["summary"]["warning_count"] == 2
        assert report["warnings"][0]["key"] == "public_api_leak_scan_freshness"
        assert "重新运行" in report["next_actions"][0]["action"]
        assert report["next_actions"][0]["owner"] == "release_operator"
        assert report["next_actions"][0]["can_auto_fix"] is True
        assert report["next_actions"][0]["verification"][0]["target"] == "docs/verification/public-api-leak-scan-latest.json"
        assert report["decision_paths"]["local_demo"]["status"] == "ready"
        assert report["decision_paths"]["local_demo"]["required_action_count"] == 0
        assert report["decision_paths"]["production_release"]["status"] == "needs_action"
        assert report["decision_paths"]["production_release"]["action_keys"] == [
            "public_api_leak_scan_freshness",
            "main_flow_runtime_smoke",
        ]

    def test_production_dry_run_uses_production_profile_without_mutating_settings(self, monkeypatch):
        from app.config import settings
        from app.services.release_readiness import build_production_dry_run_report

        monkeypatch.setattr(settings, "APP_ENV", "development")
        monkeypatch.setattr(settings, "APP_API_KEY", "")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "dev-secret-key")
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", "redis://127.0.0.1:6379/1")
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "127.0.0.1:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECURE", False)
        runtime_readiness = {
            "status": "ready",
            "checks": {
                "database": {"ok": True},
                "redis": {"ok": True},
                "minio": {"ok": True},
                "openrouter": {"ok": True},
            },
        }

        report = build_production_dry_run_report(cfg=settings, runtime_readiness=runtime_readiness)

        assert settings.APP_ENV == "development"
        assert report["report_type"] == "production_profile_dry_run"
        assert report["mutates_active_settings"] is False
        assert report["active_profile"]["mode"] == "local_mvp"
        assert report["simulated_profile"]["mode"] == "production_required"
        assert report["status"] == "fail"
        assert report["runtime_dependency_blockers"] == []
        assert "APP_API_KEY must be set in production" in report["production_config_errors"]
        assert "MinIO default credentials must not be used in production" in report["production_config_errors"]
        assert any("MINIO_SECURE must be true" in item for item in report["production_config_errors"])

    def test_api_v1_production_dry_run_exposes_non_mutating_profile_check(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings

        monkeypatch.setattr(settings, "APP_ENV", "development")

        async def _fake_ready():
            return JSONResponse(
                {
                    "status": "ready",
                    "checks": {
                        "database": {"ok": True},
                        "redis": {"ok": True},
                        "minio": {"ok": True},
                        "openrouter": {"ok": True},
                    },
                    "deployment_profile": {"mode": "local_mvp", "blocking_checks": []},
                }
            )

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)

        r = client.get("/api/v1/health/production-dry-run")

        assert r.status_code == 200
        body = r.json()
        assert settings.APP_ENV == "development"
        assert body["report_type"] == "production_profile_dry_run"
        assert body["mutates_active_settings"] is False
        assert body["simulated_profile"]["mode"] == "production_required"

    def test_api_v1_release_readiness_flags_expired_ingestion_leases(self, client, db, monkeypatch):
        from fastapi.responses import JSONResponse
        from ulid import ULID
        from app.api.v1 import health
        from app.schemas.job_run import JobRun

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)

        expired_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        job = JobRun(
            id=str(ULID()),
            job_type="ingestion",
            status="running",
            triggered_by="test",
            input_params={"source_id": "source-expired"},
            result_summary={
                "lease_state": "running",
                "lease_expires_at": expired_at.isoformat(),
            },
            retry_count=0,
        )
        db.add(job)
        db.commit()

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        assert body["status"] == "fail"
        assert checks["ingestion_queue"]["status"] == "fail"
        assert checks["ingestion_queue"]["source_endpoint"] == "/api/v1/ingestion/dispatcher/status"
        assert checks["ingestion_queue"]["signals"]["persisted_active_count"] == 1
        assert checks["ingestion_queue"]["signals"]["lease_expired_count"] == 1

    def test_api_v1_release_readiness_reports_secret_provenance_without_values(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings
        from app.services import release_readiness

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_API_KEY", "test-app-api-key")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", "redis://redis:6379/1")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setenv("APP_API_KEY_SOURCE", "sops:APP_API_KEY")
        monkeypatch.setenv("APP_SECRET_KEY_SOURCE", "sops:APP_SECRET_KEY")
        monkeypatch.setenv("OPENROUTER_API_KEY_SOURCE", "sops:OPENROUTER_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {"ok": True, "operation": "write_read_delete", "backend": "minio"},
        )

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        secrets = checks["secrets"]["signals"]["secret_provenance"]
        assert checks["secrets"]["status"] == "pass"
        assert secrets["openrouter_api_key"]["source"] == "sops"
        assert secrets["app_api_key"]["source"] == "sops"
        assert secrets["app_secret_key"]["source"] == "sops"
        assert "value" not in secrets["openrouter_api_key"]
        assert "test-openrouter-key" not in str(secrets)
        assert "OPENROUTER_KEY" not in str(secrets)

    def test_api_v1_release_readiness_reports_dependency_secret_sources_without_values(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings
        from app.services import release_readiness

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "APP_API_KEY", "test-app-api-key")
        monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 40)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(settings, "REDIS_ENABLED", True)
        monkeypatch.setattr(settings, "REDIS_URL", _test_redis_uri(0))
        monkeypatch.setattr(settings, "RATE_LIMIT_STORAGE_URI", _test_redis_uri(1))
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setenv("REDIS_URL_SOURCE", "sops:REDIS_URL")
        monkeypatch.setenv("RATE_LIMIT_STORAGE_URI_SOURCE", "sops:RATE_LIMIT_STORAGE_URI")
        monkeypatch.setenv("MINIO_ENDPOINT_SOURCE", "env:MINIO_ENDPOINT")
        monkeypatch.setenv("MINIO_BUCKET_SOURCE", "env:MINIO_BUCKET")
        monkeypatch.setenv("MINIO_ACCESS_KEY_SOURCE", "sops:MINIO_ACCESS_KEY")
        monkeypatch.setenv("MINIO_SECRET_KEY_SOURCE", "sops:MINIO_SECRET_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {"ok": True, "operation": "write_read_delete", "backend": "minio"},
        )

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        provenance = checks["secrets"]["signals"]["secret_provenance"]
        assert provenance["redis_url"]["source"] == "sops"
        assert provenance["rate_limit_storage_uri"]["source"] == "sops"
        assert provenance["minio_endpoint"]["source"] == "configured"
        assert provenance["minio_bucket"]["source"] == "configured"
        assert provenance["minio_access_key"]["source"] == "sops"
        assert provenance["minio_secret_key"]["source"] == "sops"
        assert "redis-pass" not in str(provenance)
        assert "prod-secret-key" not in str(provenance)
        assert "redis-pass" not in str(body)
        assert "prod-secret-key" not in str(body)
        assert "sops:" not in str(provenance)

    def test_api_v1_release_readiness_reports_object_storage_provider_and_tls(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings
        from app.services import release_readiness

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        def _probe(cfg):
            assert cfg.OBJECT_STORAGE_PROVIDER == "minio"
            assert cfg.MINIO_SECURE is True
            return {
                "ok": True,
                "backend": "minio",
                "provider": "minio",
                "secure": True,
                "operation": "write_read_delete",
            }

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setattr(settings, "OBJECT_STORAGE_PROVIDER", "minio")
        monkeypatch.setattr(settings, "MINIO_SECURE", True)
        monkeypatch.setattr(release_readiness, "probe_object_storage", _probe)

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        object_storage = checks["object_storage"]
        assert object_storage["signals"]["provider"] == "minio"
        assert object_storage["signals"]["secure"] is True
        assert object_storage["signals"]["probe"]["provider"] == "minio"
        assert object_storage["signals"]["probe"]["secure"] is True

    def test_api_v1_release_readiness_distinguishes_local_object_storage_probe_from_default_credentials(
        self,
        client,
        monkeypatch,
    ):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings
        from app.services import release_readiness

        async def _fake_ready():
            return JSONResponse({"status": "degraded", "checks": {}, "deployment_profile": {}})

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "APP_ENV", "development")
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "127.0.0.1:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "minioadmin")
        monkeypatch.setattr(settings, "MINIO_SECURE", False)
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {
                "ok": True,
                "backend": "minio",
                "provider": "minio",
                "secure": False,
                "operation": "write_read_delete",
            },
        )

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        object_storage = checks["object_storage"]
        assert object_storage["status"] == "warn"
        assert object_storage["signals"]["probe_ok"] is True
        assert object_storage["signals"]["production_posture_ok"] is False
        assert object_storage["signals"]["configuration_posture"] == "local_default_credentials"
        assert "MINIO_ACCESS_KEY" in object_storage["remediation"]
        assert "MINIO_SECURE" in object_storage["remediation"]
        assert "读写探针通过" in object_storage["detail"]
        assert "本地默认配置" in object_storage["detail"]

    def test_api_v1_release_readiness_fails_production_object_storage_without_tls(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings
        from app.services import release_readiness

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", True)
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setattr(settings, "MINIO_SECURE", False)
        monkeypatch.setattr(settings, "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION", False)
        monkeypatch.setenv("MINIO_ENDPOINT_SOURCE", "sops:MINIO_ENDPOINT")
        monkeypatch.setenv("MINIO_BUCKET_SOURCE", "sops:MINIO_BUCKET")
        monkeypatch.setenv("MINIO_ACCESS_KEY_SOURCE", "sops:MINIO_ACCESS_KEY")
        monkeypatch.setenv("MINIO_SECRET_KEY_SOURCE", "sops:MINIO_SECRET_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {
                "ok": True,
                "backend": "minio",
                "provider": "minio",
                "secure": False,
                "operation": "write_read_delete",
            },
        )

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        object_storage = checks["object_storage"]
        assert body["status"] == "fail"
        assert object_storage["status"] == "fail"
        assert object_storage["signals"]["tls_required"] is True
        assert object_storage["signals"]["tls_policy_ok"] is False
        assert "MINIO_SECURE=true" in object_storage["remediation"]
        assert "TLS" in object_storage["detail"]

    def test_api_v1_release_readiness_allows_explicit_insecure_object_storage_exception(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings
        from app.services import release_readiness

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", True)
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio.example.com:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setattr(settings, "MINIO_SECURE", False)
        monkeypatch.setattr(settings, "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION", True)
        monkeypatch.setenv("MINIO_ENDPOINT_SOURCE", "sops:MINIO_ENDPOINT")
        monkeypatch.setenv("MINIO_BUCKET_SOURCE", "sops:MINIO_BUCKET")
        monkeypatch.setenv("MINIO_ACCESS_KEY_SOURCE", "sops:MINIO_ACCESS_KEY")
        monkeypatch.setenv("MINIO_SECRET_KEY_SOURCE", "sops:MINIO_SECRET_KEY")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {
                "ok": True,
                "backend": "minio",
                "provider": "minio",
                "secure": False,
                "operation": "write_read_delete",
            },
        )

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        object_storage = checks["object_storage"]
        assert object_storage["status"] == "pass"
        assert object_storage["signals"]["tls_required"] is True
        assert object_storage["signals"]["tls_policy_ok"] is True
        assert object_storage["signals"]["allow_insecure_production"] is True

    def test_api_v1_release_readiness_fails_object_storage_when_probe_fails(self, client, monkeypatch):
        from fastapi.responses import JSONResponse
        from app.api.v1 import health
        from app.config import settings
        from app.services import release_readiness

        async def _fake_ready():
            return JSONResponse({"status": "ready", "checks": {}, "deployment_profile": {}})

        monkeypatch.setattr(health, "deep_health_ready", _fake_ready)
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "MINIO_ENDPOINT", "minio:9000")
        monkeypatch.setattr(settings, "MINIO_BUCKET", "agent-files")
        monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "prod-access-key")
        monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "prod-secret-key")
        monkeypatch.setattr(
            release_readiness,
            "probe_object_storage",
            lambda cfg: {
                "ok": False,
                "operation": "write_read_delete",
                "backend": "minio",
                "error": "AccessDenied",
            },
        )

        r = client.get("/api/v1/health/release-readiness")

        assert r.status_code == 200
        body = r.json()
        checks = {item["key"]: item for item in body["checks"]}
        assert body["status"] == "fail"
        assert checks["object_storage"]["status"] == "fail"
        assert checks["object_storage"]["signals"]["probe"]["ok"] is False
        assert checks["object_storage"]["signals"]["probe"]["operation"] == "write_read_delete"


# ── readiness（并行探测下游） ────────────────────────────────────


class TestHealthReady:
    def _mock_external(self, monkeypatch, redis_ok: bool, minio_ok: bool, openrouter_ok: bool):
        """把 redis/minio/openrouter 三个外部检查全 mock，只让 database 走真实路径。"""
        from app.api.v1 import health_deep

        def _redis():
            if redis_ok:
                return {"ping": True}
            raise ConnectionError("redis down (mocked)")

        def _minio():
            if minio_ok:
                return {"endpoint": "mock:9000"}
            raise ConnectionError("minio down (mocked)")

        async def _openrouter():
            if openrouter_ok:
                return {"base_url": "mock"}
            raise TimeoutError("openrouter timeout (mocked)")

        monkeypatch.setattr(health_deep, "_check_redis", _redis)
        monkeypatch.setattr(health_deep, "_check_minio", _minio)
        monkeypatch.setattr(health_deep, "_check_openrouter", _openrouter)
        monkeypatch.setattr(
            health_deep,
            "_check_ingestion_worker",
            lambda: {
                "ok": True,
                "required": False,
                "mode": "threadpool",
                "durable": False,
                "worker_reachable": None,
                "worker_count": 0,
            },
        )

    def test_ready_all_ok(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "DOCUMENT_PARSER_ADAPTER", "current")
        self._mock_external(monkeypatch, redis_ok=True, minio_ok=True, openrouter_ok=True)
        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        for name in ("database", "redis", "minio", "openrouter", "ingestion_worker"):
            assert name in body["checks"]
            assert body["checks"][name]["ok"] is True
            assert "latency_ms" in body["checks"][name]
        parser_capabilities = body["capabilities"]["document_parser"]
        assert parser_capabilities["active_adapter"] == "current"
        assert parser_capabilities["requested_adapter"] == "current"
        assert parser_capabilities["status"] == "ready"
        assert parser_capabilities["features"]["document_parse_summary"] is True
        assert parser_capabilities["features"]["table_structure"] is False

    def test_ready_degraded_when_parser_adapter_falls_back(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "DOCUMENT_PARSER_ADAPTER", "docling")
        self._mock_external(monkeypatch, redis_ok=True, minio_ok=True, openrouter_ok=True)

        r = client.get("/health/ready")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        parser_capabilities = body["capabilities"]["document_parser"]
        assert parser_capabilities["requested_adapter"] == "docling"
        assert parser_capabilities["active_adapter"] == "current"
        assert parser_capabilities["status"] == "fallback"
        assert "falling back" in parser_capabilities["notes"][0]

    def test_ready_degraded_when_soft_deps_fail(self, client, monkeypatch):
        """Redis 或 MinIO 挂掉 → degraded 200（弱依赖）"""
        self._mock_external(monkeypatch, redis_ok=False, minio_ok=False, openrouter_ok=True)
        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["checks"]["database"]["ok"] is True
        assert body["checks"]["redis"]["ok"] is False
        assert body["checks"]["minio"]["ok"] is False
        assert "error" in body["checks"]["redis"]
        assert body["deployment_profile"]["mode"] == "local_mvp"
        assert body["deployment_profile"]["allows_degraded_optional_dependencies"] is True
        assert body["deployment_profile"]["required_checks"] == ["database", "openrouter"]

    def test_ready_unhealthy_when_required_production_deps_fail(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "REQUIRE_REDIS_IN_PRODUCTION", True)
        monkeypatch.setattr(settings, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", True)
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "fake-key-for-test")
        self._mock_external(monkeypatch, redis_ok=False, minio_ok=False, openrouter_ok=True)

        r = client.get("/health/ready")

        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unhealthy"
        assert body["deployment_profile"]["mode"] == "production_required"
        assert body["deployment_profile"]["allows_degraded_optional_dependencies"] is False
        assert body["deployment_profile"]["required_checks"] == [
            "database",
            "openrouter",
            "redis",
            "minio",
            "ingestion_worker",
        ]
        assert body["deployment_profile"]["blocking_checks"] == ["redis", "minio"]

    def test_ready_unhealthy_when_openrouter_fails_and_key_present(
        self, client, monkeypatch
    ):
        """设置 OPENROUTER_API_KEY 后 openrouter 不可达 → 503 unhealthy"""
        from app.config import settings
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "fake-key-for-test")
        self._mock_external(monkeypatch, redis_ok=True, minio_ok=True, openrouter_ok=False)
        r = client.get("/health/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unhealthy"
        assert body["checks"]["openrouter"]["ok"] is False

    def test_ready_degraded_when_no_key_and_openrouter_fails(self, client, monkeypatch):
        """OPENROUTER_API_KEY 为空（离线模式）+ openrouter 不可达 → degraded（不致命）"""
        from app.config import settings
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
        self._mock_external(monkeypatch, redis_ok=True, minio_ok=True, openrouter_ok=False)
        r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"

    def test_api_v1_ready_alias_exposes_readiness_and_capabilities(self, client, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "DOCUMENT_PARSER_ADAPTER", "current")
        self._mock_external(monkeypatch, redis_ok=False, minio_ok=False, openrouter_ok=True)

        r = client.get("/api/v1/health/ready")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["checks"]["database"]["ok"] is True
        assert body["checks"]["redis"]["ok"] is False
        assert body["checks"]["minio"]["ok"] is False
        assert body["capabilities"]["document_parser"]["active_adapter"] == "current"
        assert body["deployment_profile"]["mode"] == "local_mvp"

    def test_ready_unhealthy_when_db_fails(self, client, monkeypatch):
        """DB 挂掉 → 503 unhealthy（强依赖）"""
        from app.api.v1 import health_deep
        self._mock_external(monkeypatch, redis_ok=True, minio_ok=True, openrouter_ok=True)

        def _db_down():
            raise RuntimeError("db connection refused (mocked)")

        monkeypatch.setattr(health_deep, "_check_database", _db_down)
        r = client.get("/health/ready")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "unhealthy"
        assert body["checks"]["database"]["ok"] is False
