from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


def _load_module():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "maintenance" / "sanitize_main_flow_runtime_smoke_reports.py"
    spec = importlib.util.spec_from_file_location("sanitize_main_flow_runtime_smoke_reports", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_flow_smoke_privacy_migration_is_dry_run_then_hash_only(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)
    path = report_dir / "main_flow_runtime_smoke_20260711T010203Z.json"
    private_excerpt = "private selected-source answer"
    path.write_text(
        json.dumps({"steps": {"chat": {"content_head": private_excerpt, "content_length": 30}}}),
        encoding="utf-8",
    )

    dry_run = module.sanitize_reports(report_dir, apply=False)
    assert dry_run["status"] == "pending"
    assert dry_run["replacement_count"] == 1
    assert private_excerpt in path.read_text(encoding="utf-8")

    applied = module.sanitize_reports(report_dir, apply=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_hash = f"sha256:{hashlib.sha256(private_excerpt.encode('utf-8')).hexdigest()}"

    assert applied["status"] == "applied"
    assert applied["replacement_count"] == 1
    assert "content_head" not in payload["steps"]["chat"]
    assert payload["steps"]["chat"]["legacy_content_excerpt_hash"] == expected_hash
    assert payload["privacy_migrations"][0]["migration_id"] == module.MIGRATION_ID
    assert private_excerpt not in json.dumps(applied)


def test_main_flow_smoke_privacy_migration_leaves_hash_only_report_unchanged(tmp_path):
    module = _load_module()
    report_dir = tmp_path / "docs" / "verification"
    report_dir.mkdir(parents=True)
    path = report_dir / "main_flow_runtime_smoke_latest.json"
    original = '{"steps":{"chat":{"content_hash":"sha256:abc","content_length":30}}}'
    path.write_text(original, encoding="utf-8")

    report = module.sanitize_reports(report_dir, apply=True)

    assert report["status"] == "applied"
    assert report["replacement_count"] == 0
    assert path.read_text(encoding="utf-8") == original
