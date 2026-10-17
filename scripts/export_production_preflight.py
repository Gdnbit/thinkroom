from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen


FetchJson = Callable[[str, float], dict[str, Any]]
FetchText = Callable[[str, float, dict[str, str] | None], tuple[int, dict[str, str], str]]


def project_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


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
    for marker in ("docs/verification/", "apps/backend/"):
        path_pattern = rf"(?:(?:[A-Za-z]:/|/)[^\s\"']*?{re.escape(marker)}[^\s\"',)]*)"
        normalized = re.sub(
            path_pattern,
            lambda match: marker + match.group(0).split(marker, 1)[1],
            normalized,
        )
        if normalized.startswith(marker):
            return marker + normalized.split(marker, 1)[1]
    normalized = re.sub(
        r"([a-z][a-z0-9+.-]*://)([^\s\"'/:@]+):([^\s\"'@]+)@([^\s\"']+)",
        r"\1<credentials>@\4",
        normalized,
        flags=re.IGNORECASE,
    )
    if re.match(r"^[A-Za-z]:/", normalized) and not re.search(r"\s", normalized):
        return Path(normalized).name
    if normalized.startswith("/") and "/research-os/" in normalized and not re.search(r"\s", normalized):
        return Path(normalized).name
    return normalized


def sanitize_report(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_report(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_report(item) for item in value]
    if isinstance(value, str):
        return _safe_path_string(value)
    return value


def public_cli_result(result: dict[str, str]) -> dict[str, Any]:
    return sanitize_report(result)


def format_cli_json(result: dict[str, Any]) -> str:
    return json.dumps(public_cli_result(result), ensure_ascii=True, indent=2)


def release_gate_exit_code(result: dict[str, Any], *, fail_on_not_ready: bool) -> int:
    if not fail_on_not_ready:
        return 0
    return 0 if result.get("production_ready") is True and result.get("status") == "pass" else 2


def fetch_preflight(backend_url: str, timeout: float) -> dict[str, Any]:
    url = backend_url.rstrip("/") + "/api/v1/health/production-preflight"
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Preflight endpoint returned non-object JSON")
    return data


def fetch_text_endpoint(url: str, timeout: float, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
        response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        status = int(getattr(response, "status", 200))
    return status, response_headers, payload


def _looks_like_prometheus_text(text: str, headers: dict[str, str]) -> bool:
    content_type = (headers.get("content-type") or "").lower()
    if "openmetrics" in content_type or "text/plain" in content_type:
        if "# HELP" in text or "# TYPE" in text:
            return True
    return "# HELP" in text and "# TYPE" in text


def build_observability_runtime_probe(
    *,
    backend_url: str,
    timeout: float = 10.0,
    text_fetcher: FetchText = fetch_text_endpoint,
    request_id: str = "preflight-observability-probe",
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    checks: dict[str, dict[str, Any]] = {}

    metrics_endpoint = "/metrics"
    try:
        status, headers, text = text_fetcher(backend_url.rstrip("/") + metrics_endpoint, timeout, {"Accept": "text/plain"})
        prometheus_text = _looks_like_prometheus_text(text, headers)
        checks["metrics_endpoint"] = {
            "status": "pass" if 200 <= status < 400 and prometheus_text else "warn",
            "endpoint": metrics_endpoint,
            "http_status": status,
            "content_type": headers.get("content-type", ""),
            "prometheus_text": prometheus_text,
            "required_for_production": True,
        }
    except Exception as exc:
        checks["metrics_endpoint"] = {
            "status": "warn",
            "endpoint": metrics_endpoint,
            "http_status": None,
            "prometheus_text": False,
            "required_for_production": True,
            "error_type": type(exc).__name__,
        }

    health_endpoint = "/api/v1/health"
    try:
        status, headers, _text = text_fetcher(
            backend_url.rstrip("/") + health_endpoint,
            timeout,
            {"Accept": "application/json", "X-Request-Id": request_id},
        )
        echoed = headers.get("x-request-id") == request_id
        checks["request_correlation"] = {
            "status": "pass" if 200 <= status < 400 and echoed else "warn",
            "endpoint": health_endpoint,
            "http_status": status,
            "request_id_header": "X-Request-Id",
            "echoed": echoed,
            "required_for_production": True,
        }
    except Exception as exc:
        checks["request_correlation"] = {
            "status": "warn",
            "endpoint": health_endpoint,
            "http_status": None,
            "request_id_header": "X-Request-Id",
            "echoed": False,
            "required_for_production": True,
            "error_type": type(exc).__name__,
        }

    summary = {
        "pass": sum(1 for item in checks.values() if item.get("status") == "pass"),
        "warn": sum(1 for item in checks.values() if item.get("status") == "warn"),
        "fail": sum(1 for item in checks.values() if item.get("status") == "fail"),
    }
    return {
        "report_type": "observability_runtime_probe",
        "status": "fail" if summary["fail"] else "warn" if summary["warn"] else "pass",
        "generated_at": generated.isoformat(),
        "summary": summary,
        "checks": checks,
    }


def export_preflight(
    *,
    backend_url: str,
    out_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    now: datetime | None = None,
    timeout: float = 10.0,
    fetcher: FetchJson = fetch_preflight,
    text_fetcher: FetchText = fetch_text_endpoint,
) -> dict[str, str]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    destination = Path(out_dir) if out_dir is not None else root / "docs" / "verification"
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    report = sanitize_report(fetcher(backend_url.rstrip("/"), timeout))
    if report.get("report_type") != "production_preflight":
        raise ValueError("Expected report_type=production_preflight")
    report["observability_runtime_probe"] = sanitize_report(
        build_observability_runtime_probe(
            backend_url=backend_url.rstrip("/"),
            timeout=timeout,
            text_fetcher=text_fetcher,
            generated_at=timestamp,
        )
    )
    report["exported_at"] = timestamp.isoformat()
    report["export_source"] = "/api/v1/health/production-preflight"

    export_path = destination / f"production-preflight-{stamp}.json"
    latest_path = destination / "production-preflight-latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    acceptance = report.get("acceptance") if isinstance(report.get("acceptance"), dict) else {}
    next_actions = report.get("next_actions") if isinstance(report.get("next_actions"), list) else []
    runtime_config_identity = (
        report.get("runtime_config_identity") if isinstance(report.get("runtime_config_identity"), dict) else {}
    )
    return {
        "export_path": str(export_path),
        "latest_path": str(latest_path),
        "status": str(report.get("status", "")),
        "production_ready": bool(acceptance.get("production_ready")),
        "local_demo_ready": bool(acceptance.get("local_demo_ready")),
        "runtime_config_identity": sanitize_report(runtime_config_identity),
        "next_actions": sanitize_report(next_actions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export production preflight JSON into docs/verification.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--fail-on-not-ready", action="store_true", help="Exit 2 when production_ready is false.")
    args = parser.parse_args()

    result = export_preflight(
        backend_url=args.backend_url,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        timeout=args.timeout,
    )
    print(format_cli_json(result))
    return release_gate_exit_code(result, fail_on_not_ready=args.fail_on_not_ready)


if __name__ == "__main__":
    raise SystemExit(main())
