"""Structured diagnostics for document parsing and ingestion failures."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_TEXT_SUFFIXES = {".txt", ".md"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def attempted_methods_for_file(file_path: str | None, mime_type: str | None = None) -> list[str]:
    suffix = Path(file_path or "").suffix.lower()
    mime = (mime_type or "").lower()
    if suffix in _TEXT_SUFFIXES or mime == "text/plain":
        return ["read_text"]
    if suffix == ".docx":
        return ["python_docx"]
    if suffix == ".pdf" or mime == "application/pdf":
        return [
            "pdfium_text",
            "pdfplumber_text",
            "pdf_page_render",
            "openai_vision",
            "openrouter_vision",
            "tesseract_ocr",
        ]
    if suffix in _IMAGE_SUFFIXES or mime.startswith("image/"):
        return ["openai_vision", "openrouter_vision", "tesseract_ocr"]
    return []


def build_parse_diagnostics(
    *,
    source: Any,
    reason_code: str,
    message: str,
    stage: str = "parse",
    status: str = "failed",
    detail: str | None = None,
    method_attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    storage_path = getattr(source, "storage_path", None) or ""
    suffix = Path(storage_path).suffix.lower()
    if method_attempts:
        attempted_methods = [
            item.get("method")
            for item in method_attempts
            if item.get("method") and item.get("method") != "dispatch"
        ]
    else:
        attempted_methods = attempted_methods_for_file(storage_path)
    return {
        "stage": stage,
        "status": status,
        "reason_code": reason_code,
        "message": message,
        "detail": detail,
        "source_id": getattr(source, "id", None),
        "title": getattr(source, "title", None),
        "file_type": suffix or None,
        "file_name": Path(storage_path).name if storage_path else getattr(source, "title", None),
        "attempted_methods": attempted_methods,
        "method_attempts": method_attempts or [],
        "retryable": status == "failed" and bool(attempted_methods),
    }


def encode_processing_error(message: str, diagnostics: dict[str, Any] | None = None) -> str:
    if not diagnostics:
        return message
    return json.dumps(
        {
            "message": message,
            "parse_diagnostics": diagnostics,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def decode_processing_error(raw: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw, None
    if not isinstance(parsed, dict):
        return raw, None
    message = parsed.get("message") if isinstance(parsed.get("message"), str) else raw
    diagnostics = parsed.get("parse_diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = None
    return message, diagnostics
