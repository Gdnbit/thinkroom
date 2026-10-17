from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


FetchEndpoint = Callable[[str, float], tuple[int, Any]]

DEFAULT_PUBLIC_API_ENDPOINTS = [
    "/api/v1/health",
    "/api/v1/health/ready",
    "/api/v1/health/release-readiness",
    "/api/v1/health/production-preflight",
    "/api/v1/health/production-dry-run",
    "/api/v1/health/production-secrets-tls",
]

LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("sops_source_alias", re.compile(r"\bsops:[A-Za-z0-9_:-]+")),
    ("openrouter_short_alias", re.compile(r"\bOPENROUTER_KEY\b")),
    ("host_absolute_path", re.compile(r"\b[A-Za-z]:(?:\\\\|\\|/)[^\"'\s,}]+")),
    ("minio_default_credential", re.compile(r"\bminioadmin\b")),
    ("app_default_secret", re.compile(r"\bdev-secret-key\b")),
    ("uri_embedded_credentials", re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'/:@]+:[^\s\"'@]+@")),
    ("openrouter_key_value", re.compile(r"\bsk-or-v1-[A-Za-z0-9._-]+")),
    ("sops_encrypted_blob", re.compile(r"ENC\[AES256_GCM,")),
]
MATERIALIZER_SOURCE_LABEL_PATTERN = re.compile(r"(?<=--source-label )sops:production\b")


def _safe_sample(text: str, start: int, end: int, *, context: int = 36) -> str:
    sample = text[max(0, start - context) : min(len(text), end + context)]
    sample = re.sub(r"sk-or-v1-[A-Za-z0-9._-]+", "sk-or-v1-[redacted]", sample)
    sample = re.sub(r"(?<=://)([^\s\"'/:@]+):([^\s\"'@]+)@", "<credentials>@", sample)
    sample = sample.replace("redis-pass", "[redacted]")
    return sample


def _protect_allowed_operator_labels(text: str) -> str:
    return MATERIALIZER_SOURCE_LABEL_PATTERN.sub("sops-production", text)


def find_public_report_leaks(text: str) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    searchable_text = _protect_allowed_operator_labels(text)
    for pattern_key, pattern in LEAK_PATTERNS:
        for match in pattern.finditer(searchable_text):
            leaks.append(
                {
                    "pattern_key": pattern_key,
                    "start": match.start(),
                    "end": match.end(),
                    "sample": _safe_sample(searchable_text, match.start(), match.end()),
                }
            )
    return leaks


def fetch_json_endpoint(url: str, timeout: float) -> tuple[int, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    try:
        return int(status), json.loads(payload)
    except json.JSONDecodeError:
        return int(status), payload


def _endpoint_url(backend_url: str, endpoint: str) -> str:
    return backend_url.rstrip("/") + "/" + endpoint.lstrip("/")


def build_public_api_leak_scan_report(
    *,
    backend_url: str,
    endpoints: list[str] | None = None,
    timeout: float = 10.0,
    fetcher: FetchEndpoint = fetch_json_endpoint,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows: list[dict[str, Any]] = []
    leak_count = 0
    for endpoint in endpoints or DEFAULT_PUBLIC_API_ENDPOINTS:
        url = _endpoint_url(backend_url, endpoint)
        try:
            http_status, payload = fetcher(url, timeout)
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True) if not isinstance(payload, str) else payload
            leaks = find_public_report_leaks(text)
            leak_count += len(leaks)
            status = "fail" if leaks else "pass"
            rows.append(
                {
                    "endpoint": endpoint,
                    "url": url,
                    "http_status": int(http_status),
                    "http_status_ok": 200 <= int(http_status) < 400,
                    "status": status,
                    "leak_count": len(leaks),
                    "leaks": leaks,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "endpoint": endpoint,
                    "url": url,
                    "http_status": None,
                    "status": "warn",
                    "leak_count": 0,
                    "leaks": [],
                    "error_type": type(exc).__name__,
                }
            )
    summary = {
        "pass": sum(1 for item in rows if item["status"] == "pass"),
        "warn": sum(1 for item in rows if item["status"] == "warn"),
        "fail": sum(1 for item in rows if item["status"] == "fail"),
    }
    status = "fail" if summary["fail"] else "warn" if summary["warn"] else "pass"
    return {
        "report_type": "public_api_leak_scan",
        "status": status,
        "generated_at": generated.isoformat(),
        "backend_url": backend_url.rstrip("/"),
        "endpoint_count": len(rows),
        "leak_count": leak_count,
        "summary": summary,
        "forbidden_pattern_keys": [key for key, _pattern in LEAK_PATTERNS],
        "endpoints": rows,
    }
