from __future__ import annotations

from pathlib import Path


def test_local_dependencies_script_starts_only_required_services_and_preserves_data():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "start_local_dependencies.ps1"

    text = script.read_text(encoding="utf-8")

    assert "docker compose" in text
    assert "up" in text
    assert "redis" in text
    assert "minio" in text
    assert "Ensure-MinioBucket" in text
    assert "local-dependencies-latest.json" in text
    assert '"local-dependencies-$stamp.json"' in text
    assert "Get-ProjectRelativePath" in text
    assert '$result["report_path"] = "docs/verification/' in text
    assert '$result["latest_path"] = "docs/verification/local-dependencies-latest.json"' in text
    assert "[System.Text.UTF8Encoding]::new($false)" in text
    assert "Remove-Item" not in text
    assert "docker compose down" not in text
    assert "docker volume rm" not in text


def test_local_dependencies_script_exposes_backend_env_hint_without_secret_values():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "start_local_dependencies.ps1"

    text = script.read_text(encoding="utf-8")

    assert "REDIS_ENABLED=true" in text
    assert "REDIS_URL=redis://127.0.0.1:6379/0" in text
    assert "RATE_LIMIT_STORAGE_URI=redis://127.0.0.1:6379/1" in text
    assert "MINIO_ENDPOINT=127.0.0.1:9000" in text
    assert "MINIO_BUCKET=agent-files" in text
    assert "OPENROUTER" not in text


def test_local_dependencies_script_does_not_treat_docker_info_warnings_as_daemon_failure():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "start_local_dependencies.ps1"

    text = script.read_text(encoding="utf-8")

    assert "docker info *> $null" not in text
    assert "docker info --format" in text
    assert "$ErrorActionPreference" in text


def test_sops_backend_start_can_attach_local_dependencies_without_secret_leakage():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "start_backend_sops.ps1"

    text = script.read_text(encoding="utf-8")

    assert "[switch]$UseLocalDependencies" in text
    assert "Enable-LocalDependencyEnvironment" in text
    assert '$env:REDIS_ENABLED = "true"' in text
    assert '$env:REDIS_URL = "redis://127.0.0.1:6379/0"' in text
    assert '$env:RATE_LIMIT_STORAGE_URI = "redis://127.0.0.1:6379/1"' in text
    assert '$env:MINIO_ENDPOINT = "127.0.0.1:9000"' in text
    assert '$env:MINIO_BUCKET = "agent-files"' in text
    assert '$env:MINIO_SECURE = "false"' in text
    assert 'Set-SourceMarker -Name "REDIS_URL" -Source "local-dependencies"' in text
    assert 'Set-SourceMarker -Name "MINIO_ENDPOINT" -Source "local-dependencies"' in text
    assert "OPENROUTER_API_KEY = $env:OPENROUTER_KEY" not in text
    assert "& secrets env" not in text
