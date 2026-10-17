from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _load_export_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "export_production_preflight.py"
    spec = importlib.util.spec_from_file_location("export_production_preflight", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_preflight_writes_timestamped_and_latest_json_without_host_paths(tmp_path):
    module = _load_export_module()
    project_root = Path(__file__).resolve().parents[3]
    out_dir = project_root / "tmp_preflight_export_test"
    fetched = {
        "report_type": "production_preflight",
        "status": "warn",
        "generated_at": "2026-07-08T03:30:00Z",
        "evidence_sources": {
            "ready_endpoint": "/api/v1/health/ready",
            "release_readiness_endpoint": "/api/v1/health/release-readiness",
            "local_dependency_report_path": str(project_root / "docs" / "verification" / "local-dependencies-latest.json"),
        },
        "local_dependencies": {
            "path": str(project_root / "docs" / "verification" / "local-dependencies-latest.json"),
            "status": "passed",
        },
        "acceptance": {"local_demo_ready": True, "production_ready": False},
        "runtime_config_identity": {"deployment_config_id": "prod-runtime", "set": True},
        "next_actions": [{"key": "secrets", "action": "将 APP_API_KEY 写入 SOPS/部署密钥。"}],
    }

    def fake_fetch(_url: str, _timeout: float):
        return fetched

    try:
        result = module.export_preflight(
            backend_url="http://127.0.0.1:8000",
            out_dir=out_dir,
            project_root=project_root,
            now=datetime(2026, 7, 8, 3, 30, tzinfo=timezone.utc),
            fetcher=fake_fetch,
        )

        export_path = Path(result["export_path"])
        latest_path = Path(result["latest_path"])
        assert export_path.name == "production-preflight-20260708T033000Z.json"
        assert latest_path.name == "production-preflight-latest.json"
        assert result["next_actions"] == [{"key": "secrets", "action": "将 APP_API_KEY 写入 SOPS/部署密钥。"}]
        assert result["runtime_config_identity"] == {"deployment_config_id": "prod-runtime", "set": True}
        assert export_path.exists()
        assert latest_path.exists()
        assert json.loads(export_path.read_text(encoding="utf-8")) == json.loads(latest_path.read_text(encoding="utf-8"))

        exported_text = export_path.read_text(encoding="utf-8")
        assert "E:\\\\" not in exported_text
        assert "E:/" not in exported_text
        assert "127.0.0.1" not in exported_text
        assert "localhost" not in exported_text
        assert "docs/verification/local-dependencies-latest.json" in exported_text
        assert "OPENROUTER_KEY" not in exported_text
        exported = json.loads(exported_text)
        assert exported["export_source"] == "/api/v1/health/production-preflight"
    finally:
        for path in out_dir.glob("production-preflight*.json"):
            path.unlink()
        if out_dir.exists():
            out_dir.rmdir()


def test_export_preflight_embeds_observability_runtime_probe(tmp_path):
    module = _load_export_module()
    project_root = Path(__file__).resolve().parents[3]
    out_dir = project_root / "tmp_preflight_observability_test"
    fetched = {
        "report_type": "production_preflight",
        "status": "warn",
        "generated_at": "2026-07-08T04:00:00Z",
        "acceptance": {"local_demo_ready": True, "production_ready": False},
        "next_actions": [],
    }

    def fake_fetch_preflight(_url: str, _timeout: float):
        return fetched

    def fake_fetch_text(url: str, _timeout: float, headers: dict[str, str] | None = None):
        if url.endswith("/metrics"):
            return 200, {"content-type": "text/plain; version=0.0.4"}, "# HELP http_requests_total Total requests\n# TYPE http_requests_total counter\nhttp_requests_total 1\n"
        if url.endswith("/api/v1/health"):
            request_id = (headers or {}).get("X-Request-Id", "")
            return 200, {"content-type": "application/json", "x-request-id": request_id}, '{"status":"ok"}'
        raise AssertionError(f"unexpected URL: {url}")

    try:
        result = module.export_preflight(
            backend_url="http://127.0.0.1:8000",
            out_dir=out_dir,
            project_root=project_root,
            now=datetime(2026, 7, 8, 4, 0, tzinfo=timezone.utc),
            fetcher=fake_fetch_preflight,
            text_fetcher=fake_fetch_text,
        )

        exported = json.loads(Path(result["export_path"]).read_text(encoding="utf-8"))
        probe = exported["observability_runtime_probe"]
        assert probe["status"] == "pass"
        assert probe["checks"]["metrics_endpoint"]["status"] == "pass"
        assert probe["checks"]["metrics_endpoint"]["http_status"] == 200
        assert probe["checks"]["metrics_endpoint"]["prometheus_text"] is True
        assert probe["checks"]["request_correlation"]["status"] == "pass"
        assert probe["checks"]["request_correlation"]["echoed"] is True
        assert probe["checks"]["request_correlation"]["request_id_header"] == "X-Request-Id"
        assert "127.0.0.1" not in json.dumps(probe)
    finally:
        for path in out_dir.glob("production-preflight*.json"):
            path.unlink()
        if out_dir.exists():
            out_dir.rmdir()


def test_export_preflight_public_cli_result_hides_absolute_paths():
    module = _load_export_module()
    project_root = Path(__file__).resolve().parents[3]

    cli = module.public_cli_result(
        {
            "export_path": str(project_root / "docs" / "verification" / "production-preflight-20260708T033000Z.json"),
            "latest_path": str(project_root / "docs" / "verification" / "production-preflight-latest.json"),
            "status": "warn",
        }
    )
    rendered = json.dumps(cli, ensure_ascii=False)

    assert cli["export_path"] == "docs/verification/production-preflight-20260708T033000Z.json"
    assert cli["latest_path"] == "docs/verification/production-preflight-latest.json"
    assert "E:\\" not in rendered
    assert "E:/" not in rendered


def test_export_preflight_sanitize_preserves_safe_uri_examples_in_sentences():
    module = _load_export_module()

    action = (
        "Set RATE_LIMIT_STORAGE_URI to a shared Redis URI such as "
        "redis://redis:6379/1, enable REDIS_ENABLED=true, and rerun the production preflight."
    )

    rendered = module.sanitize_report({"action": action})

    assert rendered["action"] == action


def test_export_preflight_sanitize_preserves_api_endpoint_paths():
    module = _load_export_module()

    rendered = module.sanitize_report(
        {
            "source_endpoint": "/api/v1/health/ready",
            "verification": [{"target": "/api/v1/health/production-preflight"}],
        }
    )

    assert rendered["source_endpoint"] == "/api/v1/health/ready"
    assert rendered["verification"][0]["target"] == "/api/v1/health/production-preflight"


def test_export_preflight_cli_json_is_ascii_safe_for_windows_stdout():
    module = _load_export_module()

    rendered = module.format_cli_json({"action": "将 APP_API_KEY 写入 SOPS/部署密钥。"})

    assert "部署密钥" not in rendered
    assert "\\u90e8\\u7f72\\u5bc6\\u94a5" in rendered
    assert json.loads(rendered)["action"] == "将 APP_API_KEY 写入 SOPS/部署密钥。"


def test_export_preflight_exit_code_only_fails_when_strict_gate_requested():
    module = _load_export_module()

    assert module.release_gate_exit_code({"production_ready": False, "status": "warn"}, fail_on_not_ready=False) == 0
    assert module.release_gate_exit_code({"production_ready": False, "status": "warn"}, fail_on_not_ready=True) == 2
    assert module.release_gate_exit_code({"production_ready": True, "status": "pass"}, fail_on_not_ready=True) == 0


def test_export_preflight_rejects_output_directory_outside_project(tmp_path):
    module = _load_export_module()
    project_root = Path(__file__).resolve().parents[3]

    with pytest.raises(ValueError, match="inside project"):
        module.export_preflight(
            backend_url="http://127.0.0.1:8000",
            out_dir=tmp_path,
            project_root=project_root,
            fetcher=lambda _url, _timeout: {"report_type": "production_preflight"},
        )
