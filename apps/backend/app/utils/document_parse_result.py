"""Normalized document parse result contract.

This is intentionally lightweight: it normalizes the current parser output into
a stable summary shape without introducing Docling, PaddleOCR, or other runtime
dependencies.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _attempt_methods(method_attempts: list[dict[str, Any]] | None) -> list[str]:
    return _unique_strings([
        item.get("method")
        for item in method_attempts or []
        if isinstance(item, dict) and item.get("method") != "dispatch"
    ])


def _pages_total(method_attempts: list[dict[str, Any]] | None) -> int | None:
    pages = [
        item.get("pages")
        for item in method_attempts or []
        if isinstance(item, dict) and isinstance(item.get("pages"), int)
    ]
    return max(pages) if pages else None


def _failed_pages(method_attempts: list[dict[str, Any]] | None) -> list[int]:
    values: list[int] = []
    for item in method_attempts or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("failed_pages")
        if isinstance(raw, int):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(page for page in raw if isinstance(page, int))
    return sorted(set(page for page in values if page > 0))


def _failed_slices(method_attempts: list[dict[str, Any]] | None) -> list[int]:
    values: list[int] = []
    for item in method_attempts or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("failed_slices")
        if isinstance(raw, int):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(value for value in raw if isinstance(value, int))
    return sorted(set(value for value in values if value > 0))


def _slices_total(method_attempts: list[dict[str, Any]] | None) -> int | None:
    values = [
        item.get("slices")
        for item in method_attempts or []
        if isinstance(item, dict) and isinstance(item.get("slices"), int)
    ]
    return max(values) if values else None


def _segment_type(source_type: str | None) -> str:
    normalized = (source_type or "").strip().lower()
    if normalized in {"chat", "image"}:
        return normalized
    return "document"


def _text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class DocumentParseSegment:
    type: str
    content: str
    method: str | None = None
    page: int | None = None
    ordinal: int | None = None
    source_name: str | None = None
    confidence: float | None = None
    char_start: int | None = None
    char_end: int | None = None
    aligned: bool = False

    def to_summary(self, *, excerpt_chars: int = 240) -> dict[str, Any]:
        excerpt = (self.content or "").replace("\x00", "").strip()
        if excerpt_chars >= 0:
            excerpt = excerpt[:excerpt_chars]
        data: dict[str, Any] = {
            "type": self.type,
            "chars": len(self.content or ""),
            "excerpt": excerpt,
            "text_hash": _text_hash(self.content or ""),
            "aligned": self.aligned,
        }
        if self.method:
            data["method"] = self.method
        if self.page is not None:
            data["page"] = self.page
        if self.ordinal is not None:
            data["ordinal"] = self.ordinal
        if self.source_name:
            data["source_name"] = self.source_name
        if self.confidence is not None:
            data["confidence"] = self.confidence
        if self.char_start is not None:
            data["char_start"] = self.char_start
        if self.char_end is not None:
            data["char_end"] = self.char_end
        return data

    def to_manifest(self) -> dict[str, Any]:
        data = self.to_summary(excerpt_chars=0)
        data.pop("excerpt", None)
        return data


@dataclass
class DocumentParseResult:
    text: str
    source_type: str | None = None
    methods: list[str] = field(default_factory=list)
    pages_total: int | None = None
    pages_failed: list[int] = field(default_factory=list)
    slices_total: int | None = None
    slices_failed: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    segments: list[DocumentParseSegment] = field(default_factory=list)

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        source_type: str | None = None,
        method_attempts: list[dict[str, Any]] | None = None,
        segments: list[DocumentParseSegment] | None = None,
    ) -> "DocumentParseResult":
        methods = _attempt_methods(method_attempts)
        failed_pages = _failed_pages(method_attempts)
        failed_slices = _failed_slices(method_attempts)
        warnings = ["partial_parse"] if failed_pages or failed_slices else []
        primary_method = methods[0] if methods else None
        normalized_segments = list(segments or [])
        if not normalized_segments and text:
            normalized_segments = [
                DocumentParseSegment(
                    type=_segment_type(source_type),
                    content=text,
                    method=primary_method,
                    ordinal=1,
                )
            ]

        cursor = 0
        alignment_incomplete = False
        for segment in normalized_segments:
            content = segment.content or ""
            if not content:
                segment.char_start = cursor
                segment.char_end = cursor
                segment.aligned = True
                continue
            start = (text or "").find(content, cursor)
            if start < 0:
                start = (text or "").find(content)
            if start < 0:
                segment.char_start = None
                segment.char_end = None
                segment.aligned = False
                alignment_incomplete = True
                continue
            segment.char_start = start
            segment.char_end = start + len(content)
            segment.aligned = True
            cursor = segment.char_end

        if alignment_incomplete:
            warnings.append("segment_alignment_incomplete")
        if not methods:
            methods = _unique_strings([segment.method for segment in normalized_segments])
        pages_total = _pages_total(method_attempts)
        if pages_total is None:
            page_numbers = [segment.page for segment in normalized_segments if segment.page is not None]
            pages_total = max(page_numbers) if page_numbers else None
        return cls(
            text=text or "",
            source_type=source_type,
            methods=methods,
            pages_total=pages_total,
            pages_failed=failed_pages,
            slices_total=_slices_total(method_attempts),
            slices_failed=failed_slices,
            warnings=warnings,
            segments=normalized_segments,
        )

    @property
    def status(self) -> str:
        if self.pages_failed or self.slices_failed or "segment_alignment_incomplete" in self.warnings:
            return "partial"
        return "completed" if self.text else "failed"

    def to_summary(
        self,
        *,
        segment_excerpt_chars: int = 240,
        max_segments: int = 5,
        max_manifest_segments: int = 500,
    ) -> dict[str, Any]:
        manifest_limit = max(0, max_manifest_segments)
        return {
            "status": self.status,
            "text_chars": len(self.text or ""),
            "text_hash": _text_hash(self.text),
            "segment_count": len(self.segments),
            "page_segment_count": sum(1 for segment in self.segments if segment.page is not None),
            "nonempty_page_segment_count": sum(
                1 for segment in self.segments if segment.page is not None and bool((segment.content or "").strip())
            ),
            "methods": list(self.methods),
            "pages_total": self.pages_total,
            "pages_failed": list(self.pages_failed),
            "slices_total": self.slices_total,
            "slices_failed": list(self.slices_failed),
            "warnings": list(self.warnings),
            "segments": [
                segment.to_summary(excerpt_chars=segment_excerpt_chars)
                for segment in self.segments[:max_segments]
            ],
            "segment_manifest": [
                segment.to_manifest()
                for segment in self.segments[:manifest_limit]
            ],
            "segment_manifest_truncated": len(self.segments) > manifest_limit,
        }


def build_document_parse_summary(
    text: str,
    *,
    source_type: str | None = None,
    method_attempts: list[dict[str, Any]] | None = None,
    segments: list[DocumentParseSegment] | None = None,
) -> dict[str, Any]:
    return DocumentParseResult.from_text(
        text,
        source_type=source_type,
        method_attempts=method_attempts,
        segments=segments,
    ).to_summary()
