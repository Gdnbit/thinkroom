from __future__ import annotations

import importlib.util
import json
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError


def _load_scan_script():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "scan_public_api_leaks.py"
    spec = importlib.util.spec_from_file_location("scan_public_api_leaks", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_report_leak_scanner_flags_values_not_config_keys():
    from app.verification.public_api_leak_scan import find_public_report_leaks

    payload = {
        "allowed_config_keys": ["APP_SECRET_KEY", "OPENROUTER_API_KEY", "MINIO_SECRET_KEY"],
        "unsafe": {
            "sops_alias": "sops:OPENROUTER_KEY",
            "host_path": "C:\\example\\research-os\\logs\\backend.log",
            "minio_default": "minioadmin",
            "app_default_secret": "dev-secret-key",
            "redis_url": "redis://redis-user:redis-pass@redis:6379/1",
            "openrouter_value": "sk-or-v1-test-secret-for-scanner",
        },
    }

    leaks = find_public_report_leaks(json.dumps(payload, ensure_ascii=False))

    assert {item["pattern_key"] for item in leaks} == {
        "sops_source_alias",
        "openrouter_short_alias",
        "host_absolute_path",
        "minio_default_credential",
        "app_default_secret",
        "uri_embedded_credentials",
        "openrouter_key_value",
    }
    rendered = str(leaks)
    assert "APP_SECRET_KEY" not in rendered
    assert "OPENROUTER_API_KEY" not in rendered
    assert "MINIO_SECRET_KEY" not in rendered
    assert "test-secret-for-scanner" not in rendered
    assert "redis-pass" not in rendered


def test_public_report_leak_scanner_allows_fixed_materializer_source_label_only():
    from app.verification.public_api_leak_scan import find_public_report_leaks

    leaks = find_public_report_leaks(
        json.dumps(
            {
                "operator_command": (
                    "python scripts/materialize_production_env.py "
                    "--source-label sops:production --fail-on-error"
                ),
                "unsafe_source": "sops:OPENROUTER_KEY",
            },
            ensure_ascii=False,
        )
    )

    pattern_keys = [item["pattern_key"] for item in leaks]
    assert pattern_keys.count("sops_source_alias") == 1
    assert pattern_keys.count("openrouter_short_alias") == 1
    sops_leak = next(item for item in leaks if item["pattern_key"] == "sops_source_alias")
    assert "sops:OPENROUTER_KEY" in sops_leak["sample"]
    assert "sops:production" not in sops_leak["sample"]


def test_public_api_leak_scan_report_summarizes_fetches_and_leaks():
    from app.verification.public_api_leak_scan import build_public_api_leak_scan_report

    def fake_fetch(url: str, _timeout: float):
        if url.endswith("/safe"):
            return 200, {"status": "ok", "required_key": "OPENROUTER_API_KEY"}
        if url.endswith("/unsafe"):
            return 200, {"source": "sops:OPENROUTER_KEY"}
        return 503, {"detail": "unavailable"}

    report = build_public_api_leak_scan_report(
        backend_url="http://127.0.0.1:8000",
        endpoints=["/safe", "/unsafe", "/missing"],
        fetcher=fake_fetch,
        generated_at=datetime(2026, 7, 8, 4, 30, tzinfo=timezone.utc),
    )

    assert report["report_type"] == "public_api_leak_scan"
    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 2, "warn": 0, "fail": 1}
    assert report["leak_count"] == 2
    rows = {item["endpoint"]: item for item in report["endpoints"]}
    assert rows["/safe"]["status"] == "pass"
    assert rows["/unsafe"]["status"] == "fail"
    assert rows["/unsafe"]["leaks"][0]["pattern_key"] == "sops_source_alias"
    assert rows["/missing"]["status"] == "pass"
    assert rows["/missing"]["http_status"] == 503


def test_fetch_json_endpoint_reads_http_error_response_body(monkeypatch):
    from app.verification import public_api_leak_scan

    def fake_urlopen(_request, timeout):
        assert timeout == 2.5
        raise HTTPError(
            url="http://127.0.0.1:8000/api/v1/health/ready",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=BytesIO(b'{"status":"unhealthy","checks":{"database":{"ok":false}}}'),
        )

    monkeypatch.setattr(public_api_leak_scan, "urlopen", fake_urlopen)

    status, payload = public_api_leak_scan.fetch_json_endpoint(
        "http://127.0.0.1:8000/api/v1/health/ready",
        2.5,
    )

    assert status == 503
    assert payload["status"] == "unhealthy"
    assert payload["checks"]["database"]["ok"] is False


def test_scan_public_api_leaks_script_writes_latest_report_without_host_paths(tmp_path):
    module = _load_scan_script()
    project_root = Path(__file__).resolve().parents[3]
    out_dir = project_root / "tmp_public_api_leak_scan_test"

    def fake_report(**kwargs):
        return {
            "report_type": "public_api_leak_scan",
            "status": "pass",
            "generated_at": "2026-07-08T04:30:00+00:00",
            "backend_url": kwargs["backend_url"],
            "endpoints": [],
            "summary": {"pass": 0, "warn": 0, "fail": 0},
            "leak_count": 0,
            "unsafe_path": str(project_root / "logs" / "backend.log"),
        }

    try:
        result = module.export_public_api_leak_scan(
            backend_url="http://127.0.0.1:8000",
            out_dir=out_dir,
            project_root=project_root,
            now=datetime(2026, 7, 8, 4, 30, tzinfo=timezone.utc),
            report_builder=fake_report,
        )

        export_path = Path(result["export_path"])
        latest_path = Path(result["latest_path"])
        assert export_path.name == "public-api-leak-scan-20260708T043000Z.json"
        assert latest_path.name == "public-api-leak-scan-latest.json"
        assert json.loads(export_path.read_text(encoding="utf-8")) == json.loads(
            latest_path.read_text(encoding="utf-8")
        )
        exported_text = export_path.read_text(encoding="utf-8")
        assert "E:\\" not in exported_text
        assert "E:/" not in exported_text
        assert "127.0.0.1" not in exported_text
        assert "localhost" not in exported_text
        assert "backend.log" in exported_text
        exported = json.loads(exported_text)
        assert exported["backend_url"] == "[backend-url]"
    finally:
        for path in out_dir.glob("public-api-leak-scan*.json"):
            path.unlink()
        if out_dir.exists():
            out_dir.rmdir()


def test_scan_public_api_leaks_script_returns_next_action_when_scan_warns(tmp_path):
    module = _load_scan_script()
    project_root = Path(__file__).resolve().parents[3]
    out_dir = project_root / "tmp_public_api_leak_scan_warn_test"

    def fake_report(**kwargs):
        return {
            "report_type": "public_api_leak_scan",
            "status": "warn",
            "generated_at": "2026-07-08T04:35:00+00:00",
            "backend_url": kwargs["backend_url"],
            "endpoints": [
                {
                    "endpoint": "/api/v1/health/ready",
                    "status": "warn",
                    "error_type": "HTTPError",
                    "leak_count": 0,
                    "leaks": [],
                }
            ],
            "summary": {"pass": 5, "warn": 1, "fail": 0},
            "leak_count": 0,
        }

    try:
        result = module.export_public_api_leak_scan(
            backend_url="http://127.0.0.1:8000",
            out_dir=out_dir,
            project_root=project_root,
            now=datetime(2026, 7, 8, 4, 35, tzinfo=timezone.utc),
            report_builder=fake_report,
        )

        assert result["status"] == "warn"
        assert result["leak_count"] == "0"
        assert result["next_actions"] == [
            {
                "key": "public_api_leak_scan",
                "label": "Public API leak scan",
                "status": "warn",
                "category": "public_api_leak_scan",
                "action": "Fix warned or failed public operational endpoint probes and rerun scripts/scan_public_api_leaks.py.",
                "owner": "release_operator",
                "can_auto_fix": True,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
                "verification": [
                    {
                        "kind": "script",
                        "target": "scripts/scan_public_api_leaks.py",
                        "expectation": "status=pass",
                    }
                ],
            }
        ]
    finally:
        for path in out_dir.glob("public-api-leak-scan*.json"):
            path.unlink()
        if out_dir.exists():
            out_dir.rmdir()


def test_public_api_leak_scan_public_cli_result_hides_absolute_paths():
    module = _load_scan_script()
    project_root = Path(__file__).resolve().parents[3]

    cli = module.public_cli_result(
        {
            "export_path": str(project_root / "docs" / "verification" / "public-api-leak-scan-20260708T043000Z.json"),
            "latest_path": str(project_root / "docs" / "verification" / "public-api-leak-scan-latest.json"),
            "status": "pass",
            "leak_count": "0",
        }
    )
    rendered = json.dumps(cli, ensure_ascii=False)

    assert cli["export_path"] == "docs/verification/public-api-leak-scan-20260708T043000Z.json"
    assert cli["latest_path"] == "docs/verification/public-api-leak-scan-latest.json"
    assert "E:\\" not in rendered
    assert "E:/" not in rendered


def test_public_api_leak_scan_exit_code_only_fails_when_strict_gate_requested():
    module = _load_scan_script()

    assert module.leak_scan_exit_code({"status": "fail"}, fail_on_leak=False) == 0
    assert module.leak_scan_exit_code({"status": "warn"}, fail_on_leak=True) == 2
    assert module.leak_scan_exit_code({"status": "fail"}, fail_on_leak=True) == 2
    assert module.leak_scan_exit_code({"status": "pass"}, fail_on_leak=True) == 0
