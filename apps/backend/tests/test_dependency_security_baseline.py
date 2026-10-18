import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS = REPO_ROOT / "apps" / "backend" / "requirements.txt"
REQUIREMENTS_IN = REPO_ROOT / "apps" / "backend" / "requirements.in"
REQUIREMENTS_DEV = REPO_ROOT / "apps" / "backend" / "requirements-dev.txt"
REQUIREMENTS_DEV_IN = REPO_ROOT / "apps" / "backend" / "requirements-dev.in"
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit_backend_python.ps1"
COMPILE_SCRIPT = REPO_ROOT / "scripts" / "compile_backend_requirements.ps1"
SETUP_VENV_SCRIPT = REPO_ROOT / "scripts" / "setup_backend_venv.ps1"
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_local_stack.ps1"
LIVE_SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_live_main_flow.ps1"
CORE_MODES_SMOKE_SCRIPT = REPO_ROOT / "scripts" / "smoke_core_modes.ps1"
START_BACKEND_SOPS = REPO_ROOT / "scripts" / "start_backend_sops.ps1"
UI_ACCEPTANCE_SCRIPT = REPO_ROOT / "apps" / "frontend" / "scripts" / "runDecisionChatUiAcceptance.mjs"
REAL_SCOPE_CHAT_ACCEPTANCE = (
    REPO_ROOT / "apps" / "backend" / "app" / "verification" / "real_scope_chat_acceptance.py"
)
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BACKEND_DOCKERFILE = REPO_ROOT / "apps" / "backend" / "Dockerfile"
BACKEND_DOCKERFILE_DEV = REPO_ROOT / "apps" / "backend" / "Dockerfile.dev"
PRODUCTION_ENV_EXAMPLE = REPO_ROOT / "apps" / "backend" / ".env.prod.example"
PRODUCTION_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEPLOYMENT_RUNBOOK = REPO_ROOT / "docs" / "operations" / "DEPLOYMENT_RUNBOOK.md"
SECURITY_CHECKLIST = REPO_ROOT / "docs" / "operations" / "SECURITY_CHECKLIST.md"
SELECTED_SCOPE_SUFFICIENCY_SERVICE = (
    REPO_ROOT / "apps" / "backend" / "app" / "services" / "selected_scope_sufficiency.py"
)
COMMITTEE_WORKFLOW = REPO_ROOT / "apps" / "backend" / "app" / "workflows" / "committee_workflow.py"
BRIEFINGS_API = REPO_ROOT / "apps" / "backend" / "app" / "api" / "v1" / "briefings.py"
BACKEND_TEST_CONFTEST = REPO_ROOT / "apps" / "backend" / "tests" / "conftest.py"
BACKEND_PYTEST_INI = REPO_ROOT / "apps" / "backend" / "pytest.ini"
VERIFICATION_DIR = REPO_ROOT / "docs" / "verification"
RELEASE_GATE_TEST = REPO_ROOT / "apps" / "backend" / "tests" / "test_release_gate_script.py"
FRONTEND_RELEASE_CHECK_SCRIPT = REPO_ROOT / "scripts" / "run_frontend_release_checks.py"


def _requirements_text() -> str:
    return REQUIREMENTS.read_text(encoding="utf-8")


def _workflow_step_block(workflow: str, step_name: str) -> str:
    marker = f"      - name: {step_name}"
    assert marker in workflow
    start = workflow.index(marker)
    next_start = workflow.find("\n      - name:", start + len(marker))
    if next_start == -1:
        return workflow[start:]
    return workflow[start:next_start]


def test_backend_dependency_pins_are_on_security_baseline():
    requirements = _requirements_text()

    assert "fastapi==0.138.0" in requirements
    assert "starlette==1.3.1" in requirements
    assert "python-multipart==0.0.32" in requirements


def test_backend_runtime_requirements_are_split_from_dev_tools():
    runtime = _requirements_text().lower()
    runtime_source = REQUIREMENTS_IN.read_text(encoding="utf-8").lower()
    dev = REQUIREMENTS_DEV.read_text(encoding="utf-8").lower()
    dev_source = REQUIREMENTS_DEV_IN.read_text(encoding="utf-8").lower()

    assert "pytest==" not in runtime
    assert "pytest-asyncio==" not in runtime
    assert "black==" not in runtime
    assert "mypy==" not in runtime
    assert "pytest==9.0.3" in dev
    assert "pytest-asyncio==1.4.0" in dev
    assert "pytest==9.0.3" in dev_source
    assert "fastapi==0.138.0" in runtime_source


def test_backend_ulid_dependency_matches_no_arg_constructor_contract():
    runtime = _requirements_text().lower()
    runtime_source = REQUIREMENTS_IN.read_text(encoding="utf-8").lower()

    assert "python-ulid==" in runtime_source
    assert "python-ulid==" in runtime
    assert "ulid-py" not in runtime_source
    assert "ulid-py" not in runtime


def test_backend_dependency_audit_script_is_replayable_on_windows():
    script = AUDIT_SCRIPT.read_text(encoding="utf-8")

    assert "PYTHONUTF8" in script
    assert "pip-audit" in script
    assert "backend-python-audit-latest.json" in script
    assert "--progress-spinner" in script
    assert "--cache-dir" in script
    assert ".venv" in script
    assert "D:\\Programs\\Python313\\python.exe" not in script


def test_backend_dependency_compile_script_is_replayable():
    script = COMPILE_SCRIPT.read_text(encoding="utf-8")

    assert "uv pip compile" in script
    assert "requirements.in" in script
    assert "requirements-dev.in" in script
    assert "--python-version" in script
    assert "--universal" in script


def test_backend_dependency_audit_is_part_of_ci():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "audit_backend_python.ps1" in workflow
    assert "pip-audit" in workflow
    assert "requirements-dev.txt" in workflow


def test_test_report_isolation_check_is_part_of_ci():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "check_test_report_isolation.py apps/backend/tests" in workflow


def test_production_compose_config_check_is_part_of_ci():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "check_production_compose_config.py --env-file apps/backend/.env.prod.example --fail-on-error" in workflow


def test_ci_does_not_store_literal_credential_uris():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert not re.search(r"[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@", workflow, re.I)
    expected_dsn = '"postgresql://drill:${PGPASSWORD}"' + "@localhost:5432/drill_db"
    assert expected_dsn in workflow


def test_release_gate_tests_do_not_store_literal_secret_shapes():
    test_source = RELEASE_GATE_TEST.read_text(encoding="utf-8")

    assert not re.search(r"sk-or-v1-[A-Za-z0-9_-]{12,}", test_source)
    assert not re.search(r"[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@", test_source, re.I)


def test_deployment_runbook_does_not_store_credential_uri_examples():
    runbook = DEPLOYMENT_RUNBOOK.read_text(encoding="utf-8")

    assert not re.search(r"[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@", runbook, re.I)
    assert "<POSTGRES_DSN_FROM_CONTROLLED_SECRET_SOURCE>" in runbook


def test_verification_report_hygiene_check_is_part_of_ci():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Verification report hygiene guard" in workflow
    assert "python scripts/check_verification_reports.py docs/verification --fail-on-issue" in workflow


def test_frontend_lint_is_part_of_ci():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    script = FRONTEND_RELEASE_CHECK_SCRIPT.read_text(encoding="utf-8")
    block = _workflow_step_block(workflow, "Frontend release evidence")

    assert "Frontend release evidence" in workflow
    assert "python scripts/run_frontend_release_checks.py --fail-on-error" in block
    assert '"args": ("run", "lint")' in script


def test_frontend_dependency_audit_is_part_of_ci():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    script = FRONTEND_RELEASE_CHECK_SCRIPT.read_text(encoding="utf-8")
    block = _workflow_step_block(workflow, "Frontend release evidence")

    assert "Frontend release evidence" in workflow
    assert "python scripts/run_frontend_release_checks.py --fail-on-error" in block
    assert '"args": ("run", "security:audit")' in script


def test_frontend_release_evidence_artifact_is_uploaded_by_ci():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    block = _workflow_step_block(workflow, "Upload frontend release evidence")

    assert "uses: actions/upload-artifact@v4" in block
    assert "if: always()" in block
    assert "name: frontend-release-checks" in block
    assert "docs/verification/frontend-release-checks-latest.json" in block


def test_security_checklist_tracks_online_test_isolation_as_completed():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    pytest_config = BACKEND_PYTEST_INI.read_text(encoding="utf-8")
    checklist = SECURITY_CHECKLIST.read_text(encoding="utf-8")

    assert "online: tests that require external network" in pytest_config
    assert 'python -m pytest tests/ -m "not online"' in workflow
    assert 'P1 TODO' not in checklist
    assert '引入 `@pytest.mark.online` marker' not in checklist


def test_security_checklist_header_tracks_latest_ci_control_update():
    checklist = SECURITY_CHECKLIST.read_text(encoding="utf-8")

    assert "> 最近更新:2026-07-12" in checklist
    assert "CI 离线全量测试" in checklist
    assert "前端 release evidence" in checklist
    assert "run_frontend_release_checks.py --fail-on-error" in checklist
    assert "frontend-release-checks-latest.json" in checklist
    assert "@pytest.mark.online" in checklist
    assert "P0-6 · 持久化摄取队列与 Worker 隔离" in checklist
    assert "acks_late=true" in checklist
    assert "ingestion_worker" in checklist


def test_ci_alembic_head_check_is_not_pinned_to_old_revision():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "alembic heads" in workflow
    assert "EXPECTED_HEAD" in workflow
    assert "0012_drill_pass_fixes" not in workflow


def test_ci_backend_docker_smoke_does_not_bypass_production_guard_with_dev_config():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    block = _workflow_step_block(workflow, "Smoke test backend and frontend containers (non-production liveness)")

    assert "Smoke test backend and frontend containers (non-production liveness)" in workflow
    assert "-e APP_ENV=test" in block
    assert "-e APP_ENV=production" not in block
    assert "-e DATABASE_URL=sqlite:///./smoke.db" in block


def test_ci_backend_image_asserts_production_guard_fails_closed():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    block = _workflow_step_block(
        workflow,
        "Production guard rejects unsafe backend container config",
    )

    assert "cleanup_backend_production_guard()" in block
    assert "trap cleanup_backend_production_guard EXIT" in block
    assert "docker run -d --name ros-backend-production-guard" in block
    assert "docker inspect -f '{{.State.Status}}' ros-backend-production-guard" in block
    assert "docker inspect -f '{{.State.ExitCode}}' ros-backend-production-guard" in block
    assert "docker logs ros-backend-production-guard" in block
    assert "docker rm -f ros-backend-production-guard" in block
    assert "-e APP_ENV=production" in block
    assert "-e DATABASE_URL=sqlite:///./smoke.db" in block
    assert "docker run --rm --name ros-backend-production-guard" not in block
    assert 'if [ "$container_status" != "exited" ]; then' in block
    assert 'if [ "$exit_code" -eq 0 ]; then' in block
    assert "expected production guard container to exit" in block
    assert "expected production guard to reject unsafe config" in block
    assert "ProductionConfigError" in block


def test_ci_docker_smoke_steps_cleanup_containers_on_failure():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    block = _workflow_step_block(workflow, "Smoke test backend and frontend containers (non-production liveness)")

    assert "cleanup_container_smoke()" in block
    assert "trap cleanup_container_smoke EXIT" in block
    assert "docker rm -f ros-frontend-smoke ros-backend-smoke" in block
    assert "docker network rm ros-smoke-net" in block


def test_ci_docker_release_boundary_steps_use_strict_shell():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    production_guard = _workflow_step_block(
        workflow,
        "Production guard rejects unsafe backend container config",
    )
    smoke = _workflow_step_block(workflow, "Smoke test backend and frontend containers (non-production liveness)")

    assert "set -euo pipefail" in production_guard
    assert "set -euo pipefail" in smoke


def test_ci_frontend_docker_smoke_waits_for_health_condition():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    block = _workflow_step_block(workflow, "Smoke test backend and frontend containers (non-production liveness)")

    assert "sleep 3" not in block
    assert "frontend_healthy=false" in block
    assert "for i in {1..30}" in block
    assert "curl -fsS -o /dev/null http://localhost/health" in block
    assert 'if [ "$frontend_healthy" != "true" ]; then' in block
    assert "frontend container did not become healthy" in block


def test_ci_docker_smoke_steps_have_clear_failure_diagnostics():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    backend_block = _workflow_step_block(
        workflow, "Smoke test backend and frontend containers (non-production liveness)"
    )
    frontend_block = backend_block

    assert "backend_healthy=false" in backend_block
    assert "backend_healthy=true" in backend_block
    assert 'if [ "$backend_healthy" != "true" ]; then' in backend_block
    assert "backend container did not become healthy" in backend_block
    assert "docker logs ros-backend-smoke --tail 30" in backend_block

    assert "frontend container did not become healthy" in frontend_block
    assert "docker logs ros-frontend-smoke --tail 30" in frontend_block


def test_ci_frontend_smoke_has_a_real_backend_upstream():
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    block = _workflow_step_block(workflow, "Smoke test backend and frontend containers (non-production liveness)")
    assert "docker network create ros-smoke-net" in block
    assert "--network ros-smoke-net --network-alias backend" in block
    assert "--name ros-frontend-smoke --network ros-smoke-net" in block
    assert block.index("backend healthy") < block.index("docker run -d --name ros-frontend-smoke")
    assert "curl -fsS http://localhost/api/v1/health" in block


def test_production_compose_requires_secret_source_metadata_for_release_gate():
    compose = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    assert "SECRETS_SOURCE: ${SECRETS_SOURCE:?" in compose
    assert "secret source provenance" in compose
    assert "DATABASE_URL_SOURCE: ${DATABASE_URL_SOURCE:?" in compose
    assert "REDIS_URL_SOURCE: ${REDIS_URL_SOURCE:?" in compose
    assert "MINIO_ENDPOINT_SOURCE: ${MINIO_ENDPOINT_SOURCE:?" in compose
    assert "MINIO_ACCESS_KEY_SOURCE: ${MINIO_ACCESS_KEY_SOURCE:?" in compose
    assert "MINIO_SECRET_KEY_SOURCE: ${MINIO_SECRET_KEY_SOURCE:?" in compose
    assert "APP_API_KEY_SOURCE" not in compose
    assert ("OPENROUTER" + "_KEY") not in compose


def test_production_env_example_includes_secret_source_metadata():
    env_example = PRODUCTION_ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "SECRETS_SOURCE=sops:production" in env_example
    assert "DATABASE_URL=<POSTGRES_DSN_FROM_CONTROLLED_SECRET_SOURCE>" in env_example
    assert "DATABASE_URL_SOURCE=sops:DATABASE_URL" in env_example
    assert "REDIS_URL_SOURCE=sops:REDIS_URL" in env_example
    assert "MINIO_ENDPOINT_SOURCE=sops:MINIO_ENDPOINT" in env_example
    assert "MINIO_ACCESS_KEY_SOURCE=sops:MINIO_ACCESS_KEY" in env_example
    assert "MINIO_SECRET_KEY_SOURCE=sops:MINIO_SECRET_KEY" in env_example
    assert "POSTGRES_PASSWORD=<REQUIRED_POSTGRES_PASSWORD>" in env_example
    assert "MINIO_ROOT_USER=<REQUIRED_MINIO_ROOT_USER>" in env_example
    assert "MINIO_ROOT_PASSWORD=<REQUIRED_MINIO_ROOT_PASSWORD>" in env_example
    assert "controlled secret source" in env_example
    assert not re.search(r"[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@", env_example, re.I)
    assert "APP_API_KEY_SOURCE" not in env_example
    assert ("OPENROUTER" + "_KEY_SOURCE") not in env_example


def test_deployment_runbook_documents_secret_source_provenance_gate():
    runbook = DEPLOYMENT_RUNBOOK.read_text(encoding="utf-8")

    assert "--env-file apps/backend/.env.prod -f docker-compose.prod.yml build" in runbook
    assert "--env-file apps/backend/.env.prod -f docker-compose.prod.yml run --rm migrate" in runbook
    assert "--env-file apps/backend/.env.prod -f docker-compose.prod.yml up -d" in runbook
    assert "SECRETS_SOURCE=sops:production" in runbook
    assert "POSTGRES_PASSWORD=<REQUIRED_POSTGRES_PASSWORD>" in runbook
    assert "MINIO_ROOT_USER=<REQUIRED_MINIO_ROOT_USER>" in runbook
    assert "MINIO_ROOT_PASSWORD=<REQUIRED_MINIO_ROOT_PASSWORD>" in runbook
    assert "DATABASE_URL_SOURCE=sops:DATABASE_URL" in runbook
    assert "REDIS_URL_SOURCE=sops:REDIS_URL" in runbook
    assert "MINIO_ENDPOINT_SOURCE=sops:MINIO_ENDPOINT" in runbook
    assert "MINIO_ACCESS_KEY_SOURCE=sops:MINIO_ACCESS_KEY" in runbook
    assert "MINIO_SECRET_KEY_SOURCE=sops:MINIO_SECRET_KEY" in runbook
    assert "controlled secret source" in runbook
    assert "validate_secret_inventory.py --inventory docs/verification/release-gate-secret-inventory-latest.json --env-file apps/backend/.env.prod --fail-on-error" in runbook
    assert "source_provenance_ok=true" in runbook
    assert "secret_inventory_validation.status=pass" in runbook
    assert "steps.production_compose_config.status=pass" in runbook
    assert "production_compose_config_latest_path" in runbook


def test_deployment_runbook_documents_private_env_git_safety_gate():
    runbook = DEPLOYMENT_RUNBOOK.read_text(encoding="utf-8")

    assert "env_file_git_safety" in runbook
    assert "env_file_tracked_by_git" in runbook
    assert "env_file_not_gitignored" in runbook
    assert "真实 `.env.prod` 必须保持 untracked 且被 `.gitignore` 覆盖" in runbook


def test_deployment_runbook_documents_operator_preflight_entrypoint():
    runbook = DEPLOYMENT_RUNBOOK.read_text(encoding="utf-8")

    assert "run_production_operator_preflight.py --env-file apps/backend/.env.prod --fail-on-blocked" in runbook
    assert "run_production_operator_preflight.py --env-file apps/backend/.env.prod --include-live-gate" in runbook
    assert "production-operator-preflight-latest.json" in runbook
    assert "默认不启动或停止任何容器" in runbook


def test_deployment_runbook_documents_production_stack_release_wrapper():
    runbook = DEPLOYMENT_RUNBOOK.read_text(encoding="utf-8")

    assert "run_production_stack_release.py --env-file apps/backend/.env.prod" in runbook
    assert "run_production_stack_release.py --env-file apps/backend/.env.prod --execute" in runbook
    assert "production-stack-release-latest.json" in runbook
    assert "默认只生成发布计划，不执行 Docker" in runbook
    assert "只有显式传入 `--execute` 才会执行 build / migrate / up" in runbook


def test_deployment_runbook_documents_production_readiness_summary():
    runbook = DEPLOYMENT_RUNBOOK.read_text(encoding="utf-8")

    assert "run_production_readiness_refresh.py --env-file apps/backend/.env.prod" in runbook
    assert "production-readiness-refresh-latest.json" in runbook
    assert "verification-report-hygiene-latest.json" in runbook
    assert "production_readiness_refresh.steps" in runbook
    assert "candidate_env_mismatch_count=0" in runbook
    assert "summarize_production_readiness.py --report-dir docs/verification" in runbook
    assert "production-readiness-summary-latest.json" in runbook
    assert "production-readiness-action-plan-latest.md" in runbook
    assert "action_groups" in runbook
    assert "verification_commands" in runbook
    assert "critical_secrets" in runbook
    assert "database_runtime" in runbook
    assert "priority" in runbook
    assert "split_from_config_keys" in runbook
    assert "split_from_multi_key_action_count" in runbook
    assert "top_remediation" in runbook
    assert "top_remediation_limit" in runbook
    assert "readiness_state=release_ready" in runbook
    assert "blocking_reports=[]" in runbook
    assert "missing_reports=[]" in runbook
    assert "stale_reports=[]" in runbook


def test_backend_docker_uses_runtime_requirements_only():
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    dev_dockerfile = BACKEND_DOCKERFILE_DEV.read_text(encoding="utf-8")

    assert "requirements.txt" in dockerfile
    assert "requirements-dev.txt" not in dockerfile
    assert "requirements-dev.txt" in dev_dockerfile


def test_backend_venv_setup_script_makes_isolated_environment_replayable():
    assert SETUP_VENV_SCRIPT.exists()
    script = SETUP_VENV_SCRIPT.read_text(encoding="utf-8")

    assert ".venv" in script
    assert "requirements.txt" in script
    assert "requirements-dev.txt" in script
    assert "python-ulid" in script
    assert "ULID()" in script
    assert "pip check" in script


def test_backend_startup_prefers_project_venv_without_hardcoded_machine_python():
    script = START_BACKEND_SOPS.read_text(encoding="utf-8")

    assert ".venv" in script
    assert "& secrets env" not in script
    assert "OPENROUTER_API_KEY" in script
    assert "D:\\Programs\\Python313\\python.exe" not in script


def test_backend_startup_writes_default_project_pid_file_with_lf():
    script = START_BACKEND_SOPS.read_text(encoding="utf-8")

    assert 'Join-Path $ProjectRoot ".pids\\backend.pid"' in script
    assert "Wait-ProjectBackendListener" in script
    assert "[System.IO.File]::WriteAllText" in script
    assert '"$($listenerPid)`n"' in script
    assert "Out-File -FilePath $PidFile" not in script


def test_local_stack_smoke_checks_backend_health_and_frontend_build():
    assert SMOKE_SCRIPT.exists()
    script = SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "/health/live" in script
    assert "/health/ready" in script
    assert "npm.cmd" in script
    assert "npm run build" in script


def test_backend_pytest_database_url_can_be_isolated_by_runner():
    conftest = BACKEND_TEST_CONFTEST.read_text(encoding="utf-8")

    assert "PYTEST_DATABASE_URL" in conftest
    assert "sqlite:///./test.db" in conftest


def test_live_main_flow_smoke_uses_sops_and_real_chat_scope():
    assert LIVE_SMOKE_SCRIPT.exists()
    script = LIVE_SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "start_backend_sops.ps1" in script
    assert "/api/v1/settings/test-connection" in script
    assert "/api/v1/chat/sessions" in script
    assert "source_ids" in script
    assert "llm_trace" in script
    assert "call_count" in script
    assert "$referenceTime = [string]$chat.reference_time" in script
    assert "$content.Contains($referenceTime)" in script
    assert "live-main-flow-smoke-latest.json" in script
    assert "OPENROUTER_API_KEY" not in script


def test_real_scope_acceptance_checks_reference_time_visible_in_content():
    script = REAL_SCOPE_CHAT_ACCEPTANCE.read_text(encoding="utf-8")

    assert "chat_reference_time_visible_in_content" in script
    assert "reference_time in content" in script


def test_core_modes_smoke_wraps_scope_acceptance_contract():
    assert CORE_MODES_SMOKE_SCRIPT.exists()
    script = CORE_MODES_SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "app.verification.scope_acceptance" in script
    assert "core-modes-smoke-latest.json" in script
    assert "chat_evidence_payload_ready" in script
    assert "briefing_evidence_payload_ready" in script
    assert "committee_evidence_payload_ready" in script
    assert "ui_chat_session_contains_chat_briefing_committee_messages" in script
    assert "OPENROUTER_MOCK" in script


def test_smoke_scripts_write_strict_json_without_bom():
    smoke_scripts = [
        SMOKE_SCRIPT,
        LIVE_SMOKE_SCRIPT,
        CORE_MODES_SMOKE_SCRIPT,
    ]
    latest_reports = [
        VERIFICATION_DIR / "local-stack-smoke-latest.json",
        VERIFICATION_DIR / "live-main-flow-smoke-latest.json",
        VERIFICATION_DIR / "core-modes-smoke-latest.json",
    ]

    for script_path in smoke_scripts:
        script = script_path.read_text(encoding="utf-8")
        assert "[System.IO.File]::WriteAllText" in script
        assert "UTF8Encoding" in script
        assert "Out-File -FilePath $ReportPath -Encoding utf8" not in script

    # Original machine reports are intentionally excluded from public source.
    # Report output behavior is exercised with synthetic tmp_path fixtures by
    # the smoke-script tests; archived artifacts are not a regression fixture.


def test_ui_acceptance_script_preflights_seeded_session_visibility():
    script = UI_ACCEPTANCE_SCRIPT.read_text(encoding="utf-8")

    assert "verifySeededSessionVisible" in script
    assert "/api/v1/chat/sessions/" in script
    assert "DATABASE_URL" in script
    assert "seeded session is not visible" in script
    assert "PREFLIGHT_DEADLINE_MS" in script
    assert "Connection: 'close'" in script
    assert "read ECONNRESET" in script


def test_selected_scope_sufficiency_is_not_owned_by_chat_api_layer():
    assert SELECTED_SCOPE_SUFFICIENCY_SERVICE.exists()
    forbidden = "app.api.v1.chat import selected_scope_insufficiency_reason"

    assert forbidden not in COMMITTEE_WORKFLOW.read_text(encoding="utf-8")
    assert forbidden not in BRIEFINGS_API.read_text(encoding="utf-8")
