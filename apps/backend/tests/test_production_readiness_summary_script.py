from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "summarize_production_readiness.py"
    spec = importlib.util.spec_from_file_location("summarize_production_readiness", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_report(report_dir: Path, name: str, payload: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _passing_reports(generated_at: str) -> dict[str, dict]:
    return {
        "production-env-validation-latest.json": {
            "report_type": "production_env_validation",
            "status": "pass",
            "release_allowed": True,
            "generated_at": generated_at,
            "next_actions": [],
        },
        "production-compose-config-latest.json": {
            "report_type": "production_compose_config",
            "status": "pass",
            "compose_config_ok": True,
            "generated_at": generated_at,
            "next_actions": [],
        },
        "public-api-leak-scan-latest.json": {
            "report_type": "public_api_leak_scan",
            "status": "pass",
            "leak_count": 0,
            "generated_at": generated_at,
        },
        "frontend-release-checks-latest.json": {
            "report_type": "frontend_release_checks",
            "status": "pass",
            "release_allowed": True,
            "failed_command_count": 0,
            "generated_at": generated_at,
            "next_actions": [],
        },
        "decision-quality-live-audit-latest.json": {
            "report_type": "decision_quality_live_audit",
            "status": "passed",
            "generated_at": generated_at,
            "trial_count": 21,
            "passed_trial_count": 21,
            "errors": [],
            "route_identity": {
                "scope": "decision_chat_selected_source",
                "fingerprint": "sha256:route-a",
            },
        },
        "ingestion-quality-offline-latest.json": {
            "report_type": "ingestion_quality_evaluation",
            "status": "passed",
            "mode": "offline",
            "generated_at": generated_at,
            "parser_fingerprint": "sha256:parser-a",
            "parser_identity": {
                "scope": "document_parser",
                "fingerprint": "sha256:parser-a",
            },
        },
        "ingestion-quality-online-latest.json": {
            "report_type": "ingestion_quality_evaluation",
            "status": "passed",
            "mode": "online",
            "generated_at": generated_at,
            "parser_fingerprint": "sha256:parser-a",
            "parser_identity": {
                "scope": "document_parser",
                "fingerprint": "sha256:parser-a",
            },
        },
        "structured-workflow-quality-live-audit-latest.json": {
            "report_type": "structured_workflow_quality_live_audit",
            "status": "passed",
            "generated_at": generated_at,
            "trial_count": 18,
            "errors": [],
            "route_identities": {
                "briefing": {
                    "scope": "briefing_selected_source",
                    "fingerprint": "sha256:briefing-route-a",
                },
                "committee": {
                    "scope": "committee_selected_source",
                    "fingerprint": "sha256:committee-route-a",
                },
            },
        },
        "production-preflight-latest.json": {
            "report_type": "production_preflight",
            "status": "pass",
            "generated_at": generated_at,
            "next_actions": [],
            "decision_chat_route_identity": {
                "scope": "decision_chat_selected_source",
                "fingerprint": "sha256:route-a",
            },
            "briefing_route_identity": {
                "scope": "briefing_selected_source",
                "fingerprint": "sha256:briefing-route-a",
            },
            "committee_route_identity": {
                "scope": "committee_selected_source",
                "fingerprint": "sha256:committee-route-a",
            },
            "document_parser_identity": {
                "scope": "document_parser",
                "fingerprint": "sha256:parser-a",
            },
        },
        "production-operator-preflight-latest.json": {
            "report_type": "production_operator_preflight",
            "status": "pass",
            "release_candidate_ready": True,
            "generated_at": generated_at,
            "blocking_steps": [],
            "next_actions": [],
        },
        "production-stack-release-latest.json": {
            "report_type": "production_stack_release",
            "status": "pass",
            "release_candidate_ready": True,
            "release_executed": True,
            "generated_at": generated_at,
            "blocking_steps": [],
            "next_actions": [],
        },
        "release-gate-latest.json": {
            "report_type": "release_gate",
            "status": "pass",
            "release_allowed": True,
            "generated_at": generated_at,
            "blocking_steps": [],
            "required_actions": [],
        },
        "release-gate-secret-inventory-latest.json": {
            "report_type": "release_gate_secret_inventory",
            "status": "required",
            "release_allowed": True,
            "generated_at": generated_at,
        },
        "secret-inventory-validation-latest.json": {
            "report_type": "secret_inventory_validation",
            "status": "pass",
            "generated_at": generated_at,
            "next_actions": [],
        },
    }


def test_readiness_summary_reports_release_ready_when_all_required_reports_pass(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    for name, payload in _passing_reports(now.isoformat()).items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "pass"
    assert result["readiness_state"] == "release_ready"
    assert result["release_allowed"] is True
    assert result["blocking_report_count"] == 0
    assert result["missing_report_count"] == 0
    assert result["stale_report_count"] == 0
    assert result["route_identity_mismatch_count"] == 0
    assert result["next_action_count"] == 0
    assert result["action_group_count"] == 0
    assert result["action_groups"] == []
    assert result["top_remediation_limit"] == 10
    assert result["top_remediation_count"] == 0
    assert result["top_remediation"] == []
    assert len(result["reports"]) == 14
    assert result["latest_path"] == "docs/verification/production-readiness-summary-latest.json"
    assert result["action_plan_latest_path"] == "docs/verification/production-readiness-action-plan-latest.md"
    assert result["digest_latest_path"] == "docs/verification/production-readiness-digest-latest.json"
    assert (report_dir / "production-readiness-summary-latest.json").exists()
    assert (report_dir / "production-readiness-action-plan-latest.md").exists()
    digest = json.loads((report_dir / "production-readiness-digest-latest.json").read_text(encoding="utf-8"))
    assert digest["report_type"] == "production_readiness_digest"
    assert digest["status"] == "pass"
    assert digest["readiness_state"] == "release_ready"
    assert digest["release_allowed"] is True
    assert digest["blocker_count"] == 0
    assert digest["route_identity_mismatch_count"] == 0
    assert digest["top_remediation_count"] == 0
    assert digest["blockers"] == []
    assert str(tmp_path) not in rendered


def test_readiness_summary_blocks_when_decision_quality_audit_is_missing(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports.pop("decision-quality-live-audit-latest.json")
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )

    assert result["status"] == "blocked"
    assert result["release_allowed"] is False
    assert result["missing_reports"] == ["decision_quality_live_audit"]
    digest = json.loads((report_dir / "production-readiness-digest-latest.json").read_text(encoding="utf-8"))
    assert digest["report_type"] == "production_readiness_digest"
    assert digest["status"] == "blocked"
    assert digest["readiness_state"] == "blocked"
    assert digest["release_allowed"] is False
    assert digest["blocker_count"] == 0
    assert digest["missing_reports"] == ["decision_quality_live_audit"]


def test_readiness_summary_blocks_when_decision_quality_route_identity_is_missing_or_mismatched(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)

    for reason, quality_identity, runtime_identity in (
        ("quality_report_route_fingerprint_missing", {}, {"fingerprint": "sha256:route-a"}),
        ("runtime_route_fingerprint_missing", {"fingerprint": "sha256:route-a"}, {}),
        (
            "quality_report_route_fingerprint_mismatch",
            {"fingerprint": "sha256:route-b"},
            {"fingerprint": "sha256:route-a"},
        ),
    ):
        case_root = tmp_path / reason
        report_dir = case_root / "docs" / "verification"
        reports = _passing_reports(now.isoformat())
        reports["decision-quality-live-audit-latest.json"]["route_identity"] = quality_identity
        reports["production-preflight-latest.json"]["decision_chat_route_identity"] = runtime_identity
        for name, payload in reports.items():
            _write_report(report_dir, name, payload)

        result = module.summarize_production_readiness(
            report_dir=report_dir,
            out_dir=report_dir,
            project_root=case_root,
            now=now,
        )

        assert result["status"] == "blocked"
        assert result["blocking_reports"] == ["decision_quality_live_audit"]
        assert result["route_identity_mismatch_count"] == 1
        assert result["route_identity_mismatches"][0]["reason"] == reason
        assert result["next_actions"][0]["key"] == "decision_quality_route_identity"
        assert "--allow-external-model-calls" in result["next_actions"][0]["verification_commands"][0]
        digest = json.loads((report_dir / "production-readiness-digest-latest.json").read_text(encoding="utf-8"))
        assert digest["route_identity_mismatch_count"] == 1


def test_readiness_summary_binds_structured_workflow_audit_to_both_runtime_routes(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["structured-workflow-quality-live-audit-latest.json"]["route_identities"][
        "committee"
    ]["fingerprint"] = "sha256:stale-committee-route"
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )

    assert result["status"] == "blocked"
    assert result["blocking_reports"] == ["structured_workflow_quality_live_audit"]
    assert result["route_identity_mismatch_count"] == 1
    mismatch = result["route_identity_mismatches"][0]
    assert mismatch["workflow"] == "committee"
    assert mismatch["reason"] == "quality_report_committee_route_fingerprint_mismatch"
    assert result["next_actions"][0]["key"] == "structured_workflow_quality_route_identity"
    assert "structured_workflow_quality_live_runner" in result["next_actions"][0][
        "verification_commands"
    ][0]


def test_readiness_summary_binds_both_ingestion_reports_to_current_parser(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["ingestion-quality-online-latest.json"]["parser_identity"]["fingerprint"] = (
        "sha256:stale-parser"
    )
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )

    assert result["status"] == "blocked"
    assert result["blocking_reports"] == ["ingestion_quality_online"]
    assert result["route_identity_mismatch_count"] == 1
    mismatch = result["route_identity_mismatches"][0]
    assert mismatch["reason"] == "quality_report_parser_fingerprint_mismatch"
    assert result["next_actions"][0]["key"] == "ingestion_quality_parser_identity"
    assert "ingestion_quality_eval" in result["next_actions"][0]["verification_commands"][0]


def test_readiness_summary_blocks_when_latest_env_report_uses_template_candidate(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["production-env-validation-latest.json"]["env_path"] = "apps/backend/.env.prod.example"
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )
    digest = json.loads((report_dir / "production-readiness-digest-latest.json").read_text(encoding="utf-8"))
    action_plan = (report_dir / "production-readiness-action-plan-latest.md").read_text(encoding="utf-8")

    assert result["status"] == "blocked"
    assert result["readiness_state"] == "blocked"
    assert result["release_allowed"] is False
    assert result["blocking_reports"] == ["production_env_validation"]
    assert result["candidate_env_mismatch_count"] == 1
    assert result["candidate_env_mismatches"] == [
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
    assert result["next_actions"][0]["key"] == "candidate_env_file"
    assert result["next_actions"][0]["source_report"] == "production_env_validation"
    assert "apps/backend/.env.prod.example" in result["next_actions"][0]["action"]
    assert "apps/backend/.env.prod" in result["next_actions"][0]["action"]
    assert result["next_actions"][0]["verification_commands"][0] == (
        "python scripts/materialize_production_env.py --dry-run "
        "--source-label sops:production --fail-on-error"
    )
    assert digest["candidate_env_mismatch_count"] == 1
    assert digest["candidate_env_mismatches"] == result["candidate_env_mismatches"]
    assert "Candidate production env file" in action_plan
    assert "apps/backend/.env.prod.example" in action_plan
    assert (
        "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error"
        in action_plan
    )
    assert "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error" in action_plan


def test_readiness_summary_puts_safe_materialization_before_env_file_validation(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 10, 9, 30, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["production-env-validation-latest.json"].update(
        {
            "status": "fail",
            "release_allowed": False,
            "next_actions": [
                {
                    "key": "env_file",
                    "label": "Production env file",
                    "status": "fail",
                    "category": "configuration",
                    "action": "Materialize the approved apps/backend/.env.prod candidate.",
                    "requires_secret_update": True,
                    "production_required": True,
                    "config_keys": [],
                }
            ],
        }
    )
    reports["production-compose-config-latest.json"].update(
        {
            "status": "fail",
            "compose_config_ok": False,
            "next_actions": [
                {
                    "key": "production_compose_config",
                    "label": "Missing production env candidate",
                    "status": "fail",
                    "category": "production_compose_config",
                    "action": "Materialize the approved apps/backend/.env.prod candidate.",
                    "requires_secret_update": True,
                    "production_required": True,
                    "config_keys": [],
                }
            ],
        }
    )
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )

    groups = {group["group_key"]: group for group in result["action_groups"]}
    expected_prefix = [
        "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error",
        "python scripts/materialize_production_env.py --overwrite --source-label sops:production --fail-on-error",
    ]
    assert groups["action:env_file"]["verification_commands"][:2] == expected_prefix
    assert groups["action:production_compose_config"]["verification_commands"][:2] == expected_prefix


def test_readiness_summary_blocks_on_failed_missing_and_stale_reports(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    stale_time = (now - timedelta(hours=36)).isoformat()
    reports = _passing_reports(now.isoformat())
    reports["production-env-validation-latest.json"].update(
        {
            "status": "fail",
            "release_allowed": False,
            "next_actions": [
                {
                    "key": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "config_keys": ["DATABASE_URL"],
                }
            ],
        }
    )
    reports["production-stack-release-latest.json"].update(
        {
            "status": "blocked",
            "release_candidate_ready": False,
            "blocking_steps": ["static_operator_preflight"],
            "next_actions": [{"key": "APP_API_KEY", "status": "fail", "category": "secrets"}],
        }
    )
    reports["public-api-leak-scan-latest.json"]["generated_at"] = stale_time
    reports.pop("secret-inventory-validation-latest.json")
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
        max_age_hours=24,
    )

    assert result["status"] == "blocked"
    assert result["readiness_state"] == "blocked"
    assert result["release_allowed"] is False
    assert result["blocking_report_count"] == 3
    assert result["missing_report_count"] == 1
    assert result["stale_report_count"] == 1
    assert result["next_action_count"] == 2
    assert result["missing_reports"] == ["secret_inventory_validation"]
    assert result["stale_reports"] == ["public_api_leak_scan"]
    assert result["blocking_reports"] == [
        "production_env_validation",
        "public_api_leak_scan",
        "production_stack_release",
    ]
    assert result["next_actions"][0]["source_report"] == "production_env_validation"
    assert result["next_actions"][0]["key"] == "DATABASE_URL"
    assert result["next_actions"][1]["source_report"] == "production_stack_release"
    assert result["next_actions"][1]["key"] == "APP_API_KEY"


def test_readiness_summary_requires_frontend_release_checks_report(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports.pop("frontend-release-checks-latest.json")
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )

    assert result["status"] == "blocked"
    assert result["release_allowed"] is False
    assert result["missing_reports"] == ["frontend_release_checks"]
    assert result["reports"][3]["key"] == "frontend_release_checks"
    assert result["reports"][3]["status"] == "missing"


def test_readiness_summary_groups_duplicate_actions_and_writes_operator_checklist(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["production-env-validation-latest.json"].update(
        {
            "status": "fail",
            "release_allowed": False,
            "next_actions": [
                {
                    "key": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "config_keys": ["DATABASE_URL"],
                    "requires_secret_update": True,
                },
                {
                    "key": "APP_API_KEY",
                    "status": "fail",
                    "category": "secrets",
                    "config_keys": ["APP_API_KEY"],
                    "requires_secret_update": True,
                },
            ],
        }
    )
    reports["production-operator-preflight-latest.json"].update(
        {
            "status": "blocked",
            "release_candidate_ready": False,
            "blocking_steps": ["production_env_validation"],
            "next_actions": [
                {
                    "key": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "config_keys": ["DATABASE_URL"],
                    "requires_secret_update": True,
                }
            ],
        }
    )
    reports["release-gate-latest.json"].update(
        {
            "status": "blocked",
            "release_allowed": False,
            "blocking_steps": ["production_env_validation"],
            "required_actions": [
                {
                    "key": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "config_keys": ["DATABASE_URL"],
                    "requires_secret_update": True,
                },
                {
                    "key": "POSTGRES_PASSWORD",
                    "status": "fail",
                    "category": "compose_deployment_secret",
                    "config_keys": ["POSTGRES_PASSWORD"],
                    "requires_secret_update": True,
                },
                {
                    "key": "MODEL_SECRETS",
                    "status": "fail",
                    "category": "secrets",
                    "config_keys": ["APP_API_KEY", "APP_SECRET_KEY", "OPENROUTER_API_KEY"],
                    "requires_secret_update": True,
                },
            ],
        }
    )
    reports["secret-inventory-validation-latest.json"].update(
        {
            "status": "fail",
            "next_actions": [
                {
                    "key": "DATABASE_URL",
                    "status": "fail",
                    "category": "database",
                    "config_keys": ["DATABASE_URL"],
                    "requires_secret_update": True,
                }
            ],
        }
    )
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
        top_remediation_limit=3,
    )
    groups = {group["group_key"]: group for group in result["action_groups"]}
    checklist = (report_dir / "production-readiness-action-plan-latest.md").read_text(encoding="utf-8")
    top_summary = checklist.split("## Checklist", 1)[0]

    assert result["action_group_count"] == 5
    assert result["top_remediation_limit"] == 3
    assert result["top_remediation_count"] == 3
    assert [item["group_key"] for item in result["top_remediation"]] == [
        "config:APP_API_KEY",
        "config:APP_SECRET_KEY",
        "config:OPENROUTER_API_KEY",
    ]
    assert set(groups) == {
        "config:APP_API_KEY",
        "config:APP_SECRET_KEY",
        "config:DATABASE_URL",
        "config:OPENROUTER_API_KEY",
        "config:POSTGRES_PASSWORD",
    }
    assert "config:APP_API_KEY,APP_SECRET_KEY,OPENROUTER_API_KEY" not in groups
    assert [group["group_key"] for group in result["action_groups"]] == [
        "config:APP_API_KEY",
        "config:APP_SECRET_KEY",
        "config:OPENROUTER_API_KEY",
        "config:DATABASE_URL",
        "config:POSTGRES_PASSWORD",
    ]
    assert groups["config:APP_API_KEY"]["action_count"] == 2
    assert groups["config:APP_API_KEY"]["section_key"] == "critical_secrets"
    assert groups["config:APP_API_KEY"]["section_label"] == "Critical Secrets"
    assert groups["config:APP_API_KEY"]["priority"] == 10
    materialize_prefix = [
        "python scripts/materialize_production_env.py --dry-run --source-label sops:production --fail-on-error",
        "python scripts/materialize_production_env.py --overwrite --source-label sops:production --fail-on-error",
    ]
    assert groups["config:APP_API_KEY"]["verification_commands"][:2] == materialize_prefix
    assert groups["config:APP_SECRET_KEY"]["source_reports"] == ["release_gate"]
    assert groups["config:APP_SECRET_KEY"]["split_from_multi_key_action_count"] == 1
    assert groups["config:APP_SECRET_KEY"]["split_from_config_keys"] == [
        ["APP_API_KEY", "APP_SECRET_KEY", "OPENROUTER_API_KEY"]
    ]
    assert groups["config:OPENROUTER_API_KEY"]["source_reports"] == ["release_gate"]
    assert groups["config:DATABASE_URL"]["section_key"] == "database_runtime"
    assert groups["config:DATABASE_URL"]["section_label"] == "Database And Runtime Identity"
    assert groups["config:DATABASE_URL"]["priority"] == 20
    assert groups["config:POSTGRES_PASSWORD"]["section_key"] == "database_runtime"
    assert groups["config:POSTGRES_PASSWORD"]["priority"] == 20
    assert groups["config:DATABASE_URL"]["action_count"] == 4
    assert groups["config:DATABASE_URL"]["config_keys"] == ["DATABASE_URL"]
    assert groups["config:DATABASE_URL"]["source_reports"] == [
        "production_env_validation",
        "production_operator_preflight",
        "release_gate",
        "secret_inventory_validation",
    ]
    assert groups["config:DATABASE_URL"]["requires_secret_update"] is True
    assert groups["config:DATABASE_URL"]["verification_commands"] == [
        *materialize_prefix,
        "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
        "python scripts/run_production_operator_preflight.py --env-file apps/backend/.env.prod --fail-on-blocked",
        "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url [backend-url]",
        "python scripts/validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --env-file apps/backend/.env.prod --fail-on-error",
    ]
    assert groups["config:POSTGRES_PASSWORD"]["categories"] == ["compose_deployment_secret"]
    assert result["action_plan_latest_path"] == "docs/verification/production-readiness-action-plan-latest.md"
    assert result["digest_latest_path"] == "docs/verification/production-readiness-digest-latest.json"
    assert "## Next Remediation Summary" in checklist
    assert "Showing `3` of `5` action groups" in checklist
    assert "- [ ] `APP_API_KEY` (`Critical Secrets`)" in top_summary
    assert "- [ ] `APP_SECRET_KEY` (`Critical Secrets`)" in top_summary
    assert "- [ ] `OPENROUTER_API_KEY` (`Critical Secrets`)" in top_summary
    assert "`DATABASE_URL`" not in top_summary
    assert "## 1. Critical Secrets" in checklist
    assert "## 2. Database And Runtime Identity" in checklist
    assert checklist.index("## 1. Critical Secrets") < checklist.index("## 2. Database And Runtime Identity")
    assert checklist.index("- [ ] `APP_API_KEY`") < checklist.index("- [ ] `DATABASE_URL`")
    assert "- [ ] `APP_SECRET_KEY`" in checklist
    assert "- [ ] `OPENROUTER_API_KEY`" in checklist
    assert "`APP_API_KEY, APP_SECRET_KEY, OPENROUTER_API_KEY`" not in checklist
    assert "- [ ] `DATABASE_URL`" in checklist
    assert "Sources: production_env_validation, production_operator_preflight, release_gate" in checklist
    assert "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error" in checklist
    assert (
        "python scripts/validate_secret_inventory.py --inventory "
        "docs/verification/release-gate-secret-inventory-latest.json "
        "--env-file apps/backend/.env.prod --fail-on-error"
    ) in checklist
    digest = json.loads((report_dir / "production-readiness-digest-latest.json").read_text(encoding="utf-8"))
    digest_rendered = json.dumps(digest, ensure_ascii=False)

    assert digest["report_type"] == "production_readiness_digest"
    assert digest["status"] == "blocked"
    assert digest["readiness_state"] == "blocked"
    assert digest["release_allowed"] is False
    assert digest["blocker_count"] == 5
    assert digest["blocker_total_count"] == 5
    assert digest["blocker_shown_count"] == 3
    assert digest["top_remediation_limit"] == 3
    assert digest["top_remediation_count"] == 3
    assert [blocker["group_key"] for blocker in digest["blockers"]] == [
        "config:APP_API_KEY",
        "config:APP_SECRET_KEY",
        "config:OPENROUTER_API_KEY",
    ]
    assert digest["blockers"][0] == {
        "group_key": "config:APP_API_KEY",
        "label": "APP_API_KEY",
        "section_key": "critical_secrets",
        "section_label": "Critical Secrets",
        "priority": 10,
        "config_keys": ["APP_API_KEY"],
        "referenced_config_keys": ["APP_API_KEY"],
        "categories": ["secrets"],
        "source_reports": ["production_env_validation", "release_gate"],
        "requires_secret_update": True,
        "production_required": False,
        "split_from_multi_key_action_count": 1,
        "verification_commands": [
            *materialize_prefix,
            "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
            "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url [backend-url]",
        ],
    }
    assert "sample_actions" not in digest_rendered
    assert "next_actions" not in digest_rendered
    assert "required_actions" not in digest_rendered
    assert str(tmp_path) not in checklist
    assert str(tmp_path) not in digest_rendered


def test_readiness_summary_preserves_release_gate_operator_action_groups(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["release-gate-latest.json"].update(
        {
            "status": "blocked",
            "release_allowed": False,
            "blocking_steps": ["release_readiness"],
            "required_actions": [
                {
                    "key": "runtime_profile",
                    "label": "Runtime profile",
                    "status": "warn",
                    "config_keys": ["APP_ENV", "APP_DEBUG"],
                    "action": "Switch to the production runtime profile.",
                },
                {
                    "key": "secrets",
                    "label": "Secrets",
                    "status": "warn",
                    "config_keys": ["APP_API_KEY", "APP_SECRET_KEY", "OPENROUTER_API_KEY"],
                    "action": "Inject application model secrets through SOPS.",
                },
                {
                    "key": "object_storage",
                    "label": "Object storage",
                    "status": "warn",
                    "config_keys": ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_SECURE"],
                    "action": "Replace local object-storage defaults.",
                },
                {
                    "key": "observability",
                    "label": "Observability",
                    "status": "warn",
                    "config_keys": ["LOG_FORMAT", "SENTRY_DSN"],
                    "action": "Enable structured logs and error tracking.",
                },
                {
                    "key": "production_dry_run",
                    "label": "Production dry run",
                    "status": "warn",
                    "category": "production_profile",
                    "config_keys": [
                        "APP_ENV",
                        "DEPLOYMENT_CONFIG_ID",
                        "DATABASE_URL",
                        "APP_DEBUG",
                        "APP_API_KEY",
                        "APP_SECRET_KEY",
                        "OPENROUTER_API_KEY",
                        "REDIS_ENABLED",
                        "RATE_LIMIT_STORAGE_URI",
                    ],
                    "action": "Rerun the production dry-run after env materialization.",
                },
                {
                    "key": "rate_limit_storage",
                    "label": "Rate limit storage",
                    "status": "warn",
                    "config_keys": ["RATE_LIMIT_STORAGE_URI", "REDIS_ENABLED"],
                    "action": "Use Redis-backed rate-limit storage for production.",
                },
            ],
        }
    )
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )
    group_keys = [group["group_key"] for group in result["action_groups"]]
    digest = json.loads((report_dir / "production-readiness-digest-latest.json").read_text(encoding="utf-8"))
    digest_group_keys = [blocker["group_key"] for blocker in digest["blockers"]]

    assert group_keys == [
        "action:secrets",
        "action:object_storage",
        "action:observability",
        "action:production_dry_run",
        "action:rate_limit_storage",
        "action:runtime_profile",
    ]
    assert digest["blocker_count"] == 6
    assert digest_group_keys == group_keys
    assert not any(key.startswith("config:APP_API_KEY") for key in group_keys)
    assert not any(key.startswith("config:MINIO_ACCESS_KEY") for key in group_keys)
    assert not any(key.startswith("config:RATE_LIMIT_STORAGE_URI") for key in group_keys)
    groups = {group["group_key"]: group for group in result["action_groups"]}
    assert groups["action:rate_limit_storage"]["referenced_config_keys"] == [
        "RATE_LIMIT_STORAGE_URI",
        "REDIS_ENABLED",
    ]
    assert "127.0.0.1" not in json.dumps(result, ensure_ascii=False)
    assert "127.0.0.1" not in json.dumps(digest, ensure_ascii=False)
    assert groups["action:secrets"]["verification_commands"] == [
        "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod --backend-url [backend-url]"
    ]
    assert result["action_groups"][0]["section_key"] == "critical_secrets"
    assert result["action_groups"][1]["section_key"] == "object_storage"
    assert result["action_groups"][2]["section_key"] == "observability"
    assert result["action_groups"][3]["section_key"] == "production_profile"
    assert result["action_groups"][4]["section_key"] == "production_profile"
    assert result["action_groups"][5]["section_key"] == "production_profile"


def test_readiness_summary_keeps_domain_priority_when_profile_action_is_split(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["release-gate-latest.json"].update(
        {
            "status": "blocked",
            "release_allowed": False,
            "required_actions": [
                {
                    "key": "PRODUCTION_PROFILE",
                    "status": "fail",
                    "category": "production_profile",
                    "config_keys": ["APP_API_KEY", "DATABASE_URL", "APP_ENV"],
                }
            ],
        }
    )
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )
    groups = {group["group_key"]: group for group in result["action_groups"]}

    assert set(groups) == {"config:APP_API_KEY", "config:APP_ENV", "config:DATABASE_URL"}
    assert groups["config:APP_API_KEY"]["section_key"] == "critical_secrets"
    assert groups["config:DATABASE_URL"]["section_key"] == "database_runtime"
    assert groups["config:APP_ENV"]["section_key"] == "production_profile"
    assert [group["group_key"] for group in result["action_groups"]] == [
        "config:APP_API_KEY",
        "config:DATABASE_URL",
        "config:APP_ENV",
    ]


def test_readiness_summary_redacts_secret_shapes_and_absolute_paths(tmp_path):
    module = _load_module()
    now = datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)
    report_dir = tmp_path / "docs" / "verification"
    reports = _passing_reports(now.isoformat())
    reports["production-stack-release-latest.json"].update(
        {
            "status": "blocked",
            "release_candidate_ready": False,
            "blocking_steps": ["docker_build"],
            "next_actions": [
                {
                    "key": "docker_build",
                    "status": "fail",
                    "action": f"failed at {tmp_path} "
                    + "postgresql://user:"
                    + "password@example.invalid/db sk-or-v1-"
                    + ("A" * 24),
                }
            ],
        }
    )
    for name, payload in reports.items():
        _write_report(report_dir, name, payload)

    result = module.summarize_production_readiness(
        report_dir=report_dir,
        out_dir=report_dir,
        project_root=tmp_path,
        now=now,
    )
    rendered = json.dumps(result, ensure_ascii=False)

    assert "sk-or-v1-" not in rendered
    assert "postgresql://user:" + "password@" not in rendered
    assert str(tmp_path) not in rendered
    assert "[redacted-openrouter-key]" in rendered
    assert "[redacted-credential-uri]" in rendered
