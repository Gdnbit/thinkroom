from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_repository_hygiene.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_repository_hygiene", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repository_hygiene_current_tree_passes():
    module = _load_script()

    result = module.audit_repository(PROJECT_ROOT)

    assert result["status"] == "pass", result["issues"]
    assert result["counts"]["issues"] == 0


def test_repository_hygiene_identifies_active_legacy_script(tmp_path):
    module = _load_script()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "fix_migration.py").write_text("print('legacy')\n", encoding="utf-8")

    assert module.find_active_legacy_scripts(tmp_path) == ["fix_migration.py"]


def test_archive_index_is_deterministic_and_tracks_pass_continuity(tmp_path):
    module = _load_script()
    verification = (
        tmp_path
        / "docs"
        / "archive"
        / "verification-history"
        / "2026-07"
        / "runtime"
        / "smoke"
    )
    verification.mkdir(parents=True)
    (verification / "smoke-20260712T010203Z.json").write_text("{}\n", encoding="utf-8")
    optimization = tmp_path / "docs" / "archive" / "optimization-hardening" / "2026-07"
    optimization.mkdir(parents=True)
    (optimization / "2026-07-12-production-hardening-pass-1.md").write_text(
        "# pass 1\n", encoding="utf-8"
    )

    first = module.build_archive_index(tmp_path)
    second = module.build_archive_index(tmp_path)

    assert first == second
    assert first["verification_history"]["files"] == 1
    assert first["verification_history"]["families"][0]["family"] == "smoke"
    assert first["optimization_hardening"]["first_pass"] == 1
    assert first["optimization_hardening"]["last_pass"] == 1
    assert first["optimization_hardening"]["missing_passes"] == []
