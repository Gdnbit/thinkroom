from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


BLOCKED_MARKERS = (
    "agent_user",
    "db-secret",
    "dev-secret-key",
    "minioadmin",
    "should-not:leak",
    "sk-or-v1-" + "should-not-be-read",
    "sops:OPENROUTER_KEY",
    "OPENROUTER_KEY",
    "\ufffd",
)
SECRET_SHAPE_PATTERNS = (
    ("openrouter_api_key_shape", re.compile(r"sk-or-v1-[A-Za-z0-9_-]{12,}")),
    ("uri_embedded_credentials", re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@")),
    ("sops_encrypted_blob", re.compile(r"ENC\[AES256_GCM,")),
)
TEXT_ARTIFACT_PATTERNS = (
    "release-gate-action-plan-latest.md",
    "release-gate-secret-inventory-latest.md",
    "production-readiness-action-plan-latest.md",
)
PUBLIC_RELEASE_ARTIFACT_PREFIXES = (
    "release-gate",
    "production-readiness",
    "public-api-leak-scan",
)
DEFAULT_JSON_ARTIFACT_PATTERNS = (
    "*-latest.json",
    "*_latest.json",
    "main_flow_runtime_smoke*.json",
)
SOURCE_PROVENANCE_RUNBOOK = "docs/operations/PRODUCTION_SECRET_SOURCE_PROVENANCE.md"
REQUIRED_TEXT_ARTIFACT_RUNBOOKS = {
    "release-gate-action-plan-latest.md": (SOURCE_PROVENANCE_RUNBOOK,),
    "release-gate-secret-inventory-latest.md": (SOURCE_PROVENANCE_RUNBOOK,),
}
RUNBOOK_LINK_RE = re.compile(r"docs/operations/[A-Za-z0-9_.-]+\.md")
LOCAL_BACKEND_URL_RE = re.compile(r"\bhttps?://(?:127\.0\.0\.1|localhost)(?::\d+)?\b", re.IGNORECASE)


def _relative_report_path(path: Path, report_dir: Path) -> str:
    try:
        return path.relative_to(report_dir).as_posix()
    except ValueError:
        return path.name


def _issue(path: Path, report_dir: Path, code: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": _relative_report_path(path, report_dir),
        "code": code,
        "message": message,
    }
    payload.update(extra)
    return payload


def _project_root_from_report_dir(report_dir: Path) -> Path:
    if report_dir.name == "verification" and report_dir.parent.name == "docs":
        return report_dir.parent.parent
    return report_dir.parent


def _read_text_artifact(path: Path, report_dir: Path, issues: list[dict[str, Any]]) -> str | None:
    data = path.read_bytes()
    content = data
    if data.startswith(b"\xef\xbb\xbf"):
        issues.append(
            _issue(
                path,
                report_dir,
                "utf8_bom",
                "Verification artifacts must be UTF-8 without BOM.",
            )
        )
        content = data[3:]

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        issues.append(
            _issue(
                path,
                report_dir,
                "invalid_utf8",
                "Verification artifact is not valid UTF-8.",
                error=str(exc),
            )
        )
        return None


def _scan_blocked_markers(
    *,
    path: Path,
    report_dir: Path,
    text: str,
    blocked_markers: tuple[str, ...],
    issues: list[dict[str, Any]],
) -> None:
    markers = sorted(marker for marker in blocked_markers if marker in text)
    if markers:
        issues.append(
            _issue(
                path,
                report_dir,
                "blocked_marker",
                "Verification artifact contains a blocked historical leak or mojibake marker.",
                markers=markers,
            )
        )


def _scan_secret_shapes(
    *,
    path: Path,
    report_dir: Path,
    text: str,
    issues: list[dict[str, Any]],
) -> None:
    markers = sorted(name for name, pattern in SECRET_SHAPE_PATTERNS if pattern.search(text))
    if markers:
        issues.append(
            _issue(
                path,
                report_dir,
                "secret_shape_marker",
                "Verification artifact contains a secret-like token shape.",
                markers=markers,
            )
        )


def _is_public_release_artifact(path: Path) -> bool:
    return any(path.name.startswith(prefix) for prefix in PUBLIC_RELEASE_ARTIFACT_PREFIXES)


def _scan_local_backend_urls(
    *,
    path: Path,
    report_dir: Path,
    text: str,
    issues: list[dict[str, Any]],
) -> None:
    if not _is_public_release_artifact(path):
        return
    match_count = len(LOCAL_BACKEND_URL_RE.findall(text))
    if match_count:
        issues.append(
            _issue(
                path,
                report_dir,
                "local_backend_url",
                "Public release verification artifact contains a local backend URL; use [backend-url] instead.",
                match_count=match_count,
            )
        )


def _find_key_paths(value: Any, target_key: str, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}"
            if key == target_key:
                paths.append(child_path)
            paths.extend(_find_key_paths(child, target_key, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_find_key_paths(child, target_key, f"{prefix}[{index}]"))
    return paths


def _scan_main_flow_private_excerpts(
    *,
    path: Path,
    report_dir: Path,
    payload: Any,
    issues: list[dict[str, Any]],
) -> None:
    if not path.name.startswith("main_flow_runtime_smoke"):
        return
    excerpt_paths = _find_key_paths(payload, "content_head")
    if excerpt_paths:
        issues.append(
            _issue(
                path,
                report_dir,
                "private_answer_excerpt",
                "Main-flow smoke report stores a private answer excerpt; retain only a hash and length.",
                field_paths=excerpt_paths,
            )
        )


def _runbook_link_issue_message(path: Path, *, issue_kind: str) -> str:
    if path.name == "release-gate-action-plan-latest.md":
        if issue_kind == "unsafe":
            return "Release action plan references an operational runbook outside the project."
        return "Release action plan references an operational runbook that does not exist."
    if issue_kind == "unsafe":
        return "Verification text artifact references an operational runbook outside the project."
    return "Verification text artifact references an operational runbook that does not exist."


def _missing_required_runbook_issue_message(path: Path) -> str:
    if path.name == "release-gate-action-plan-latest.md":
        return "Release action plan is missing a required operational runbook link."
    return "Verification text artifact is missing a required operational runbook link."


def _scan_text_artifact_runbooks(
    *,
    path: Path,
    report_dir: Path,
    text: str,
    issues: list[dict[str, Any]],
) -> None:
    project_root = _project_root_from_report_dir(report_dir)
    links = sorted(set(RUNBOOK_LINK_RE.findall(text)))

    for required_runbook in REQUIRED_TEXT_ARTIFACT_RUNBOOKS.get(path.name, ()):
        if required_runbook not in links:
            issues.append(
                _issue(
                    path,
                    report_dir,
                    "missing_required_runbook_link",
                    _missing_required_runbook_issue_message(path),
                    runbook_path=required_runbook,
                )
            )

    for runbook_path in links:
        candidate = (project_root / runbook_path).resolve()
        try:
            candidate.relative_to(project_root.resolve())
        except ValueError:
            issues.append(
                _issue(
                    path,
                    report_dir,
                    "unsafe_runbook_link",
                    _runbook_link_issue_message(path, issue_kind="unsafe"),
                    runbook_path=runbook_path,
                )
            )
            continue
        if not candidate.is_file():
            issues.append(
                _issue(
                    path,
                    report_dir,
                    "missing_runbook_link",
                    _runbook_link_issue_message(path, issue_kind="missing"),
                    runbook_path=runbook_path,
                )
            )


def check_verification_reports(
    report_dir: str | Path = "docs/verification",
    *,
    pattern: str | None = None,
    text_patterns: tuple[str, ...] = TEXT_ARTIFACT_PATTERNS,
    blocked_markers: tuple[str, ...] = BLOCKED_MARKERS,
) -> dict[str, Any]:
    root = Path(report_dir)
    issues: list[dict[str, Any]] = []
    json_patterns = (pattern,) if pattern else DEFAULT_JSON_ARTIFACT_PATTERNS
    json_files: list[Path] = []
    if root.exists():
        seen_json_files: set[Path] = set()
        for json_pattern in json_patterns:
            for path in sorted(root.glob(json_pattern)):
                if path not in seen_json_files:
                    json_files.append(path)
                    seen_json_files.add(path)
    text_files: list[Path] = []
    if root.exists():
        for text_pattern in text_patterns:
            for path in sorted(root.glob(text_pattern)):
                if path not in json_files and path not in text_files:
                    text_files.append(path)

    for path in json_files:
        text = _read_text_artifact(path, root, issues)
        if text is None:
            continue

        payload: Any = None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            issues.append(
                _issue(
                    path,
                    root,
                    "invalid_json",
                    "Verification report is not strict JSON.",
                    error=str(exc),
                )
            )

        if payload is not None:
            _scan_main_flow_private_excerpts(
                path=path,
                report_dir=root,
                payload=payload,
                issues=issues,
            )

        _scan_blocked_markers(
            path=path,
            report_dir=root,
            text=text,
            blocked_markers=blocked_markers,
            issues=issues,
        )
        _scan_secret_shapes(
            path=path,
            report_dir=root,
            text=text,
            issues=issues,
        )
        _scan_local_backend_urls(
            path=path,
            report_dir=root,
            text=text,
            issues=issues,
        )

    for path in text_files:
        text = _read_text_artifact(path, root, issues)
        if text is None:
            continue
        _scan_blocked_markers(
            path=path,
            report_dir=root,
            text=text,
            blocked_markers=blocked_markers,
            issues=issues,
        )
        _scan_secret_shapes(
            path=path,
            report_dir=root,
            text=text,
            issues=issues,
        )
        _scan_local_backend_urls(
            path=path,
            report_dir=root,
            text=text,
            issues=issues,
        )
        _scan_text_artifact_runbooks(
            path=path,
            report_dir=root,
            text=text,
            issues=issues,
        )

    next_actions: list[dict[str, Any]] = []
    if issues:
        next_actions.append(
            {
                "key": "verification_report_hygiene",
                "label": "Verification report hygiene",
                "status": "fail",
                "category": "verification_artifacts",
                "action": "Fix invalid verification reports and rerun scripts/check_verification_reports.py.",
                "owner": "release_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
                "verification": [
                    {
                        "kind": "script",
                        "target": "scripts/check_verification_reports.py",
                        "expectation": "status=pass",
                    }
                ],
                "verification_commands": [
                    "python scripts/check_verification_reports.py docs/verification --fail-on-issue",
                ],
            }
        )

    return {
        "report_type": "verification_report_hygiene",
        "status": "pass" if not issues else "fail",
        "report_dir": root.as_posix() if not root.is_absolute() else root.name,
        "pattern": pattern or DEFAULT_JSON_ARTIFACT_PATTERNS[0],
        "json_patterns": list(json_patterns),
        "text_patterns": list(text_patterns),
        "checked_count": len(json_files) + len(text_files),
        "json_checked_count": len(json_files),
        "text_checked_count": len(text_files),
        "issue_count": len(issues),
        "issues": issues,
        "next_actions": next_actions,
    }


def public_cli_result(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_type": report.get("report_type"),
        "status": report.get("status"),
        "report_dir": report.get("report_dir"),
        "pattern": report.get("pattern"),
        "json_patterns": report.get("json_patterns", []),
        "text_patterns": report.get("text_patterns", []),
        "checked_count": report.get("checked_count"),
        "json_checked_count": report.get("json_checked_count"),
        "text_checked_count": report.get("text_checked_count"),
        "issue_count": report.get("issue_count"),
        "issues": report.get("issues", []),
    }


def exit_code(report: dict[str, Any], *, fail_on_issue: bool) -> int:
    if fail_on_issue and report.get("status") != "pass":
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check verification JSON reports for CI-safe hygiene.")
    parser.add_argument("report_dir", nargs="?", default="docs/verification")
    parser.add_argument("--pattern", default=None)
    parser.add_argument("--fail-on-issue", action="store_true")
    args = parser.parse_args()

    report = check_verification_reports(args.report_dir, pattern=args.pattern)
    print(json.dumps(public_cli_result(report), ensure_ascii=False, indent=2))
    return exit_code(report, fail_on_issue=args.fail_on_issue)


if __name__ == "__main__":
    raise SystemExit(main())
