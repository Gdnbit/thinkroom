from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_isolation_script():
    project_root = Path(__file__).resolve().parents[3]
    script = project_root / "scripts" / "check_test_report_isolation.py"
    spec = importlib.util.spec_from_file_location("check_test_report_isolation", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_isolation_scanner_flags_report_writer_without_explicit_output(tmp_path):
    module = _load_isolation_script()
    test_file = tmp_path / "test_bad_release_gate.py"
    test_file.write_text(
        """
def test_bad(module):
    module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
    )
""".lstrip(),
        encoding="utf-8",
    )

    issues = module.scan_test_report_isolation(tmp_path)

    assert len(issues) == 1
    assert issues[0]["report_writer"] == "run_release_gate"
    assert issues[0]["missing_keywords"] == ["out_dir", "project_root"]
    assert issues[0]["path"].endswith("test_bad_release_gate.py")


def test_report_isolation_scanner_accepts_explicit_temp_output(tmp_path):
    module = _load_isolation_script()
    test_file = tmp_path / "test_good_release_gate.py"
    test_file.write_text(
        """
def test_good(module, tmp_path):
    module.run_release_gate(
        backend_url="http://127.0.0.1:8000",
        timeout=20,
        out_dir=tmp_path / "docs" / "verification",
        project_root=tmp_path,
    )
""".lstrip(),
        encoding="utf-8",
    )

    issues = module.scan_test_report_isolation(tmp_path)

    assert issues == []


def test_report_isolation_scanner_flags_refresh_without_explicit_output(tmp_path):
    module = _load_isolation_script()
    test_file = tmp_path / "test_bad_refresh.py"
    test_file.write_text(
        """
def test_bad(module):
    module.refresh_production_readiness(
        env_file="apps/backend/.env.prod",
    )
""".lstrip(),
        encoding="utf-8",
    )

    issues = module.scan_test_report_isolation(tmp_path)

    assert len(issues) == 1
    assert issues[0]["report_writer"] == "refresh_production_readiness"
    assert issues[0]["missing_keywords"] == ["out_dir", "project_root"]


def test_report_isolation_scanner_flags_current_repo_regressions():
    module = _load_isolation_script()
    project_root = Path(__file__).resolve().parents[3]

    issues = module.scan_test_report_isolation(project_root / "apps" / "backend" / "tests")

    assert issues == []
