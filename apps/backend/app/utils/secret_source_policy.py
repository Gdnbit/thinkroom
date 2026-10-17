from __future__ import annotations

from typing import Any


ACCEPTED_SECRET_SOURCE_PREFIXES = (
    "sops:",
    "secret-store:",
    "vault:",
    "aws-secrets-manager:",
    "gcp-secret-manager:",
    "azure-key-vault:",
    "kubernetes-secret:",
    "doppler:",
    "1password:",
)


def source_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if ":" not in text:
        return text
    return text.split(":", 1)[0]


def trusted_secret_source(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return any(text.startswith(prefix) for prefix in ACCEPTED_SECRET_SOURCE_PREFIXES)


def public_secret_source(value: Any) -> str:
    if not value:
        return "default_or_missing"
    text = str(value)
    if trusted_secret_source(text):
        return source_family(text)
    if text in {"default_or_missing", "environment_or_settings", "local-dependencies"}:
        return text
    return "configured"


def source_provenance_metadata(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    trusted = trusted_secret_source(text)
    return {
        "source_marker_present": bool(text),
        "source_marker_family": source_family(text) if trusted else "untrusted" if text else "",
        "source_provenance_ok": trusted,
        "source_provenance_required": True,
    }
