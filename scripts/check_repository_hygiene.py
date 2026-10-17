from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CURRENT_DOCUMENTS = (
    "README.md",
    ".ai/README.md",
    ".ai/pipeline-state.md",
    "docs/README.md",
    "docs/status/CURRENT_STATE.md",
    "docs/status/KNOWN_ISSUES.md",
    "docs/status/SYSTEM_ASSESSMENT.md",
    "docs/specs/local-v0.1/SCOPE_AND_ACCEPTANCE.md",
    "docs/architecture/LOCAL_V01_CORE_WORKFLOW_ADR.md",
    "docs/guides/GUIDE.md",
    "docs/verification/EVIDENCE_INDEX.md",
    "docs/verification/v0.1/README.md",
    "docs/archive/README.md",
    "docs/archive/ARCHIVE_INDEX.json",
    "docs/archive/verification-history/README.md",
    "docs/archive/optimization-hardening/README.md",
    "docs/project/constitution.md",
    "evals/README.md",
    "handoff/README.md",
    "handoff/normalized/README.md",
    "infra/README.md",
    "infra/migration/README.md",
    "scripts/README.md",
    "scripts/diagnostics/README.md",
    "scripts/maintenance/README.md",
    "scripts/archive/legacy/README.md",
    "tests/README.md",
    "tests/load/README.md",
)

HISTORICAL_BANNER_DOCUMENTS = (
    "docs/architecture/DATA_LAYER.md",
    "docs/architecture/DATA_LAYER_QUICK_REFERENCE.md",
    "docs/architecture/DOCUMENT_INTELLIGENCE_ADR.md",
    "docs/architecture/INDUSTRY_BENCHMARK.md",
    "docs/architecture/PERSONAL_AGENT_SYSTEM.md",
    "docs/architecture/PIPELINE_ANALYSIS.md",
    "docs/architecture/RECALL_TO_ANSWER_REVIEW.md",
    "docs/architecture/STORAGE_TO_ANSWER_REVIEW.md",
    "docs/architecture/V2_DESIGN.md",
    "docs/dev-notes/G17_CONTEXTUAL_EMBEDDINGS.md",
    "docs/dev-notes/G18_AGENT_RETRIEVAL.md",
    "docs/dev-notes/G19_SPEAKER_AWARE_RERANK.md",
    "docs/dev-notes/KNOWLEDGE_GRAPH_LESSONS.md",
    "docs/dev-notes/TESTING_BLINDSPOTS.md",
    "docs/handover/BACKEND_API.md",
    "docs/handover/DATABASE.md",
    "docs/handover/FRONTEND.md",
    "docs/handover/INFRASTRUCTURE.md",
    "docs/handover/TESTING.md",
    "docs/operations/BACKUP_RESTORE.md",
    "docs/operations/DEPLOYMENT_RUNBOOK.md",
    "docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md",
    "docs/operations/SECURITY_CHECKLIST.md",
    "docs/operations/SQLITE_TO_POSTGRES_MIGRATION.md",
    "docs/reference/PRD_V1.md",
    "docs/status/ACTION_PLAN.md",
    "docs/status/GAP_LIST.md",
    "docs/status/PRE_LAUNCH_DRILL.md",
    ".ai/agent-team-log.md",
    ".ai/agent-team-plan.md",
    ".ai/confirmation/target-confirmation.md",
    ".ai/decision-log.md",
    ".ai/harness-map.md",
    ".ai/progress.md",
    "handoff/normalized/acceptance.md",
    "handoff/normalized/assumptions.md",
    "handoff/normalized/business-brief.md",
    "handoff/normalized/conflicts.md",
    "handoff/normalized/examples.catalog.md",
    "handoff/normalized/glossary.md",
    "handoff/normalized/output-contract.md",
    "handoff/normalized/rulebook.md",
    "handoff/normalized/source-inventory.md",
    "evals/ingestion-reliability/README.md",
    "evals/production-readiness/README.md",
    "infra/backup/README.md",
    "tests/load/README.md",
)

DOC_POLICY_DIRECTORIES = (
    "architecture",
    "archive",
    "dev-notes",
    "guides",
    "handover",
    "operations",
    "optimization",
    "project",
    "prototypes",
    "reference",
    "specs",
    "status",
    "verification",
    "visuals",
)

SPEC_POLICY_DIRECTORIES = (
    "decision-quality-gates",
    "ingestion-quality",
    "ingestion-reliability",
    "local-v0.1",
    "production-readiness",
)

LEGACY_ACTIVE_SCRIPT_NAMES = {
    "append_migration.py",
    "demo.py",
    "fix_migration.py",
    "fix_migration2.py",
    "init_model_registry.py",
    "seed_fund_profiles.py",
    "truncate_migration.py",
    "backend_uvicorn_launcher.ps1",
}

EXPECTED_ARCHIVED_LEGACY_SCRIPTS = LEGACY_ACTIVE_SCRIPT_NAMES
EXPECTED_MAINTENANCE_SCRIPTS = {"sanitize_main_flow_runtime_smoke_reports.py"}
CODE_EXTENSIONS = {".py", ".ps1", ".mjs", ".js", ".ts", ".tsx", ".sh"}
TEMP_TEST_NAME = re.compile(
    r"(^|[-_.])(tmp|temp|debug|scratch|oneoff|ad[-_]?hoc)([-_.]|$)", re.IGNORECASE
)
HISTORICAL_RUN_NAME = re.compile(
    r"01[0-9A-HJKMNP-TV-Z]{24}|20\d{6}T?\d{6}Z?|20\d{2}-\d{2}-\d{2}|"
    r"(?:^|[-_])20\d{6}(?:[-_.]|$)|(?:^|[-_])pass[-_]?\d+",
    re.IGNORECASE,
)
HARDENING_PASS_NAME = re.compile(r"production-hardening-pass-(\d+)\.md$", re.IGNORECASE)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

WALK_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    "htmlcov",
    "uploads",
    "backups",
    "logs",
    ".runtime_logs",
}


def _issue(code: str, path: str, detail: str) -> dict[str, str]:
    return {"code": code, "path": path, "detail": detail}


def _read_utf8(path: Path) -> tuple[str | None, str | None]:
    try:
        raw = path.read_bytes()
        return raw.decode("utf-8-sig"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, str(exc)


def _git_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {line.replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def _archive_digest(root: Path, files: list[Path]) -> tuple[str, int]:
    digest = hashlib.sha256()
    total_bytes = 0
    for path in sorted(files):
        raw = path.read_bytes()
        relative_path = path.relative_to(root).as_posix()
        file_hash = hashlib.sha256(raw).hexdigest()
        total_bytes += len(raw)
        digest.update(f"{relative_path}\0{len(raw)}\0{file_hash}\n".encode("utf-8"))
    return f"sha256:{digest.hexdigest()}", total_bytes


def _count_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def build_archive_index(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    root = root.resolve()
    verification_root = root / "docs" / "archive" / "verification-history"
    verification_files = sorted(
        path
        for path in verification_root.rglob("*")
        if path.is_file() and path.name != "README.md"
    ) if verification_root.exists() else []

    family_counts: dict[tuple[str, str, str], dict[str, Any]] = {}
    verification_months: list[str] = []
    verification_categories: list[str] = []
    verification_extensions: list[str] = []
    for path in verification_files:
        relative_parts = path.relative_to(verification_root).parts
        if len(relative_parts) < 4:
            month, category, family = "invalid", "invalid", "invalid"
        else:
            month, category, family = relative_parts[:3]
        verification_months.append(month)
        verification_categories.append(category)
        verification_extensions.append(path.suffix.lower() or "<none>")
        key = (month, category, family)
        entry = family_counts.setdefault(
            key,
            {"month": month, "category": category, "family": family, "files": 0, "bytes": 0},
        )
        entry["files"] += 1
        entry["bytes"] += path.stat().st_size

    verification_digest, verification_bytes = _archive_digest(root, verification_files)

    optimization_root = root / "docs" / "archive" / "optimization-hardening"
    optimization_files = sorted(
        path
        for path in optimization_root.rglob("*")
        if path.is_file() and HARDENING_PASS_NAME.search(path.name)
    ) if optimization_root.exists() else []
    pass_numbers = sorted(
        int(match.group(1))
        for path in optimization_files
        if (match := HARDENING_PASS_NAME.search(path.name)) is not None
    )
    maximum_pass = pass_numbers[-1] if pass_numbers else 0
    missing_passes = [number for number in range(1, maximum_pass + 1) if number not in pass_numbers]
    optimization_digest, optimization_bytes = _archive_digest(root, optimization_files)

    return {
        "schema_version": 1,
        "status": "historical_evidence_not_current_acceptance",
        "verification_history": {
            "files": len(verification_files),
            "bytes": verification_bytes,
            "content_digest": verification_digest,
            "months": _count_values(verification_months),
            "categories": _count_values(verification_categories),
            "extensions": _count_values(verification_extensions),
            "families": [family_counts[key] for key in sorted(family_counts)],
        },
        "optimization_hardening": {
            "files": len(optimization_files),
            "bytes": optimization_bytes,
            "content_digest": optimization_digest,
            "months": _count_values(
                [path.relative_to(optimization_root).parts[0] for path in optimization_files]
            ),
            "first_pass": pass_numbers[0] if pass_numbers else None,
            "last_pass": maximum_pass or None,
            "missing_passes": missing_passes,
        },
    }


def write_archive_index(root: Path = PROJECT_ROOT) -> Path:
    root = root.resolve()
    target = root / "docs" / "archive" / "ARCHIVE_INDEX.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_archive_index(root), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def _iter_project_code(root: Path):
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in WALK_EXCLUDES]
        current_path = Path(current)
        for name in files:
            path = current_path / name
            if path.suffix.lower() in CODE_EXTENSIONS:
                yield path


def find_active_legacy_scripts(root: Path) -> list[str]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(
        path.name
        for path in scripts_dir.iterdir()
        if path.is_file() and path.name in LEGACY_ACTIVE_SCRIPT_NAMES
    )


def find_unindexed_root_scripts(root: Path) -> list[str]:
    scripts_dir = root / "scripts"
    readme_text, error = _read_utf8(scripts_dir / "README.md")
    if error or readme_text is None or not scripts_dir.exists():
        return []
    return sorted(
        path.name
        for path in scripts_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in CODE_EXTENSIONS
        and path.name not in readme_text
    )


def _check_markdown_links(root: Path, relative_path: str, text: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    source = root / relative_path
    for match in MARKDOWN_LINK.finditer(text):
        target = match.group(1).strip().strip("<>")
        target_path = target.split("#", 1)[0]
        if not target_path or re.match(r"^[a-z][a-z0-9+.-]*:", target_path, re.IGNORECASE):
            continue
        resolved = (source.parent / target_path).resolve()
        if not resolved.exists():
            issues.append(_issue("broken_markdown_link", relative_path, target))
    return issues


def audit_repository(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    root = root.resolve()
    if (root / "PUBLIC_SOURCE_MANIFEST.json").is_file():
        import importlib.util
        spec = importlib.util.spec_from_file_location("public_source_check", root / "scripts/check_public_source.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.audit_public(root)
    issues: list[dict[str, str]] = []

    for relative_path in CURRENT_DOCUMENTS:
        path = root / relative_path
        if not path.is_file():
            issues.append(_issue("missing_current_document", relative_path, "required file missing"))
            continue
        raw = path.read_bytes()
        text, error = _read_utf8(path)
        if error or text is None:
            issues.append(_issue("invalid_utf8", relative_path, error or "decode failed"))
            continue
        if raw.startswith(b"\xef\xbb\xbf"):
            issues.append(_issue("unexpected_utf8_bom", relative_path, "current docs use UTF-8 without BOM"))
        if b"\r\n" in raw:
            issues.append(_issue("unexpected_crlf", relative_path, "current docs use LF"))
        if "\ufffd" in text:
            issues.append(_issue("replacement_character", relative_path, "possible mojibake"))
        issues.extend(_check_markdown_links(root, relative_path, text))

    for directory in DOC_POLICY_DIRECTORIES:
        relative_path = f"docs/{directory}/README.md"
        if not (root / relative_path).is_file():
            issues.append(_issue("missing_directory_policy", relative_path, "documentation status is undefined"))

    for directory in SPEC_POLICY_DIRECTORIES:
        relative_path = f"docs/specs/{directory}/README.md"
        if not (root / relative_path).is_file():
            issues.append(_issue("missing_spec_policy", relative_path, "specification status is undefined"))

    for relative_path in HISTORICAL_BANNER_DOCUMENTS:
        path = root / relative_path
        text, error = _read_utf8(path)
        if error or text is None:
            issues.append(_issue("missing_or_invalid_historical_document", relative_path, error or "read failed"))
            continue
        head = "\n".join(text.splitlines()[:12])
        normalized_head = head.lower()
        if not any(
            marker in normalized_head
            for marker in ("历史", "延后", "愿景", "参考", "historical", "deferred", "reference")
        ):
            issues.append(_issue("missing_status_banner", relative_path, "historical/reference status not visible"))

    verification_root = root / "docs" / "verification"
    if verification_root.exists():
        for path in sorted(verification_root.iterdir()):
            if path.is_file() and HISTORICAL_RUN_NAME.search(path.name):
                issues.append(
                    _issue(
                        "historical_run_in_active_verification_root",
                        path.relative_to(root).as_posix(),
                        "move to docs/archive/verification-history and refresh the archive index",
                    )
                )

    optimization_root = root / "docs" / "optimization"
    if optimization_root.exists():
        for path in sorted(optimization_root.iterdir()):
            if path.is_file() and HARDENING_PASS_NAME.search(path.name):
                issues.append(
                    _issue(
                        "hardening_pass_in_active_optimization_root",
                        path.relative_to(root).as_posix(),
                        "move to docs/archive/optimization-hardening and refresh the archive index",
                    )
                )

    archive_index_path = root / "docs" / "archive" / "ARCHIVE_INDEX.json"
    expected_archive_index = build_archive_index(root)
    try:
        saved_archive_index = json.loads(archive_index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(_issue("invalid_archive_index", "docs/archive/ARCHIVE_INDEX.json", str(exc)))
    else:
        if saved_archive_index != expected_archive_index:
            issues.append(
                _issue(
                    "stale_archive_index",
                    "docs/archive/ARCHIVE_INDEX.json",
                    "run python scripts/check_repository_hygiene.py --write-archive-index",
                )
            )

    readme_text, _ = _read_utf8(root / "README.md")
    if readme_text is not None:
        for marker in ("本地 v0.1", "docs/status/CURRENT_STATE.md", "check_repository_hygiene.py"):
            if marker not in readme_text:
                issues.append(_issue("root_readme_missing_current_marker", "README.md", marker))
        if "具备公网部署能力" in readme_text:
            issues.append(_issue("root_readme_overclaims_production", "README.md", "stale production claim"))

    guide_text, _ = _read_utf8(root / "docs/guides/GUIDE.md")
    if guide_text is not None and "Status: current for the accepted local v0.1 workflow" not in guide_text:
        issues.append(_issue("guide_not_current", "docs/guides/GUIDE.md", "local v0.1 status marker missing"))

    for name in find_active_legacy_scripts(root):
        issues.append(_issue("active_legacy_script", f"scripts/{name}", "move to scripts/archive/legacy"))

    for name in find_unindexed_root_scripts(root):
        issues.append(_issue("unindexed_active_script", f"scripts/{name}", "document purpose and side effects in scripts/README.md"))

    for name in sorted(EXPECTED_ARCHIVED_LEGACY_SCRIPTS):
        relative_path = f"scripts/archive/legacy/{name}"
        if not (root / relative_path).is_file():
            issues.append(_issue("missing_archived_legacy_script", relative_path, "preserved script missing"))

    for name in sorted(EXPECTED_MAINTENANCE_SCRIPTS):
        relative_path = f"scripts/maintenance/{name}"
        if not (root / relative_path).is_file():
            issues.append(_issue("missing_maintenance_script", relative_path, "tested repair utility missing"))
        if (root / "scripts" / name).exists():
            issues.append(_issue("maintenance_script_in_active_root", f"scripts/{name}", "move to scripts/maintenance"))

    frontend_package_path = root / "apps" / "frontend" / "package.json"
    try:
        frontend_package = json.loads(frontend_package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(_issue("invalid_frontend_package", "apps/frontend/package.json", str(exc)))
    else:
        unit_command = frontend_package.get("scripts", {}).get("test:unit")
        if unit_command != "node --test tests/*.test.mjs":
            issues.append(
                _issue(
                    "missing_frontend_unit_test_entry",
                    "apps/frontend/package.json",
                    "expected test:unit to run every tests/*.test.mjs file",
                )
            )

    frontend_unit_tests = sorted((root / "apps" / "frontend" / "tests").glob("*.test.mjs"))
    if not frontend_unit_tests:
        issues.append(_issue("missing_frontend_unit_tests", "apps/frontend/tests", "no *.test.mjs files found"))

    ci_text, ci_error = _read_utf8(root / ".github" / "workflows" / "ci.yml")
    if ci_error or ci_text is None or "npm run test:unit" not in ci_text:
        issues.append(
            _issue(
                "frontend_unit_tests_not_in_ci",
                ".github/workflows/ci.yml",
                "add the maintained frontend unit-test entry after npm install",
            )
        )

    tracked = _git_files(root)
    active_test_prefixes = ("apps/backend/tests/", "apps/frontend/tests/", "scripts/tests/")
    temporary_tests = sorted(
        path
        for path in tracked
        if path.startswith(active_test_prefixes) and TEMP_TEST_NAME.search(Path(path).name)
    )
    for path in temporary_tests:
        issues.append(_issue("temporary_test_in_active_suite", path, "quarantine, archive, or formalize"))

    pending_readme_path = root / "_pending_delete_review" / "README.md"
    pending_text, _ = _read_utf8(pending_readme_path)
    untracked_code: list[str] = []
    for path in _iter_project_code(root):
        relative_path = path.relative_to(root).as_posix()
        if relative_path in tracked:
            continue
        untracked_code.append(relative_path)
        if relative_path.startswith("_pending_delete_review/"):
            if pending_text is None or path.name not in pending_text:
                issues.append(_issue("undocumented_quarantined_code", relative_path, "add to pending review inventory"))
        else:
            issues.append(_issue("untracked_code_outside_quarantine", relative_path, "track, archive, or quarantine"))

    evidence_files = (
        "docs/verification/v0.1/core-workflow-live.json",
        "docs/verification/v0.1/structured-workflow-quality.json",
    )
    for relative_path in evidence_files:
        path = root / relative_path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(_issue("invalid_current_evidence", relative_path, str(exc)))
            continue
        if payload.get("status") not in {"passed", "passed_with_documented_warnings"}:
            issues.append(_issue("current_evidence_not_passed", relative_path, str(payload.get("status"))))

    doc_files = [path for path in (root / "docs").rglob("*") if path.is_file()]
    formal_tests = [
        path
        for path in tracked
        if path.startswith(active_test_prefixes)
        or path.startswith("tests/load/")
        or path.startswith("tests/e2e_inputs/")
    ]
    result = {
        "report_type": "repository_hygiene",
        "status": "pass" if not issues else "fail",
        "root": ".",
        "counts": {
            "documentation_files": len(doc_files),
            "tracked_formal_test_assets": len(formal_tests),
            "archived_legacy_scripts": len(EXPECTED_ARCHIVED_LEGACY_SCRIPTS),
            "maintenance_scripts": len(EXPECTED_MAINTENANCE_SCRIPTS),
            "frontend_unit_test_files": len(frontend_unit_tests),
            "untracked_code_files": len(untracked_code),
            "issues": len(issues),
        },
        "untracked_code_files": sorted(untracked_code),
        "issues": issues,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate project documentation and test-code hygiene.")
    parser.add_argument(
        "--write-archive-index",
        action="store_true",
        help="rewrite the deterministic historical archive index before validation",
    )
    args = parser.parse_args()
    if args.write_archive_index:
        write_archive_index()
    result = audit_repository()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
