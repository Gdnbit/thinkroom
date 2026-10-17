from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


def project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _backend_app_path(project_root: Path) -> Path:
    return project_root / "apps" / "backend"


def _ensure_backend_import_path(project_root: Path) -> None:
    backend_path = str(_backend_app_path(project_root))
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


def _ensure_inside_project(path: Path, project_root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = project_root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Output directory must be inside project: {resolved_root}") from exc
    return resolved_path


def _safe_path_string(value: str) -> str:
    normalized = value.replace("\\", "/")
    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return "[backend-url]"
    marker = "docs/verification/"
    if marker in normalized:
        return marker + normalized.split(marker, 1)[1]
    if ":/" in normalized and not any(ch.isspace() for ch in normalized):
        return Path(normalized).name
    return value


def sanitize_export(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_export(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_export(item) for item in value]
    if isinstance(value, str):
        return _safe_path_string(value)
    return value


def public_cli_result(result: dict[str, str]) -> dict[str, Any]:
    return sanitize_export(result)


def leak_scan_exit_code(result: dict[str, Any], *, fail_on_leak: bool) -> int:
    if not fail_on_leak:
        return 0
    return 0 if result.get("status") == "pass" else 2


def _next_actions_from_scan_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(report.get("status") or "unknown")
    if status == "pass":
        return []
    leak_count = int(report.get("leak_count") or 0)
    action = (
        "Remove leaked values from public operational endpoint responses and rerun scripts/scan_public_api_leaks.py."
        if leak_count
        else "Fix warned or failed public operational endpoint probes and rerun scripts/scan_public_api_leaks.py."
    )
    return [
        {
            "key": "public_api_leak_scan",
            "label": "Public API leak scan",
            "status": status,
            "category": "public_api_leak_scan",
            "action": action,
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


ReportBuilder = Callable[..., dict[str, Any]]


def export_public_api_leak_scan(
    *,
    backend_url: str,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    timeout: float = 10.0,
    report_builder: ReportBuilder | None = None,
) -> dict[str, str]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    _ensure_backend_import_path(root)
    if report_builder is None:
        from app.verification.public_api_leak_scan import build_public_api_leak_scan_report

        report_builder = build_public_api_leak_scan_report

    destination = Path(out_dir) if out_dir is not None else root / "docs" / "verification"
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    report = sanitize_export(
        report_builder(
            backend_url=backend_url.rstrip("/"),
            timeout=timeout,
            generated_at=timestamp,
        )
    )
    if report.get("report_type") != "public_api_leak_scan":
        raise ValueError("Expected report_type=public_api_leak_scan")
    report["exported_at"] = timestamp.isoformat()

    export_path = destination / f"public-api-leak-scan-{stamp}.json"
    latest_path = destination / "public-api-leak-scan-latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "export_path": str(export_path),
        "latest_path": str(latest_path),
        "status": str(report.get("status", "")),
        "leak_count": str(report.get("leak_count", "")),
        "next_actions": _next_actions_from_scan_report(report),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan public operational APIs for leaked internal values.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--fail-on-leak", action="store_true", help="Exit 2 when the scan status is not pass.")
    args = parser.parse_args()

    result = export_public_api_leak_scan(
        backend_url=args.backend_url,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        timeout=args.timeout,
    )
    print(json.dumps(public_cli_result(result), ensure_ascii=False, indent=2))
    return leak_scan_exit_code(result, fail_on_leak=args.fail_on_leak)


if __name__ == "__main__":
    raise SystemExit(main())
