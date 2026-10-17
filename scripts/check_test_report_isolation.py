from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Iterable


REPORT_WRITERS: dict[str, tuple[str, ...]] = {
    "run_release_gate": ("out_dir", "project_root"),
    "refresh_production_readiness": ("out_dir", "project_root"),
    "validate_env_file": ("out_dir", "project_root"),
    "export_preflight": ("out_dir", "project_root"),
    "export_public_api_leak_scan": ("out_dir", "project_root"),
}


def _iter_python_tests(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix == ".py":
            yield root
        return

    for path in root.rglob("*.py"):
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py"):
            yield path


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def scan_test_report_isolation(root: str | Path) -> list[dict[str, object]]:
    root_path = Path(root)
    issues: list[dict[str, object]] = []

    for path in sorted(_iter_python_tests(root_path)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            issues.append(
                {
                    "path": _relative_path(path, root_path),
                    "line": exc.lineno or 1,
                    "report_writer": "syntax_error",
                    "missing_keywords": [],
                    "message": f"Cannot parse test file: {exc.msg}",
                }
            )
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            name = _call_name(node.func)
            if name not in REPORT_WRITERS:
                continue

            present = {keyword.arg for keyword in node.keywords if keyword.arg}
            missing = [keyword for keyword in REPORT_WRITERS[name] if keyword not in present]
            if not missing:
                continue

            issues.append(
                {
                    "path": _relative_path(path, root_path),
                    "line": node.lineno,
                    "report_writer": name,
                    "missing_keywords": missing,
                    "message": (
                        "Report-writing test calls must pass explicit temporary "
                        "out_dir and project_root so real docs/verification latest "
                        "reports are not overwritten."
                    ),
                }
            )

    return issues


def build_report(root: str | Path) -> dict[str, object]:
    issues = scan_test_report_isolation(root)
    return {
        "report_type": "test_report_isolation",
        "status": "pass" if not issues else "fail",
        "issue_count": len(issues),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that report-writing tests use isolated output paths."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default="apps/backend/tests",
        help="Test file or directory to scan.",
    )
    args = parser.parse_args()

    report = build_report(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
