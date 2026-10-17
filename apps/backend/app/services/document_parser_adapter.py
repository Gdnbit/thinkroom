"""Adapter boundary for document parsing engines.

The current implementation wraps the existing ingestion file parser without
introducing a new runtime dependency. Future engines such as Docling or
PaddleOCR should implement the same boundary before they are wired into the
main ingestion workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import shutil
from typing import Any, Callable, Optional

from loguru import logger
from app.utils.document_parse_result import DocumentParseSegment


ProgressCallback = Callable[[int, int], None]
ContentReader = Callable[..., Optional[str]]
TextNormalizer = Callable[[str], str]


@dataclass(frozen=True)
class DocumentParserAdapterResult:
    text: Optional[str]
    from_cache: bool = False
    normalized: bool = False
    should_persist: bool = False


class CurrentDocumentParserAdapter:
    """Adapter for the repository's existing parser path."""

    name = "current"

    def __init__(
        self,
        *,
        content_reader: ContentReader,
        normalizer: TextNormalizer,
        requested_name: str = "current",
    ) -> None:
        self.content_reader = content_reader
        self.normalizer = normalizer
        self.requested_name = requested_name

    def parse(
        self,
        source: Any,
        *,
        progress_cb: Optional[ProgressCallback] = None,
        diagnostics: Optional[list[dict]] = None,
        segment_collector: Optional[list[DocumentParseSegment]] = None,
    ) -> DocumentParserAdapterResult:
        raw_text = getattr(source, "raw_text", None)
        if raw_text:
            normalized = self.normalizer(raw_text)
            if segment_collector is not None:
                segment_collector.append(
                    DocumentParseSegment(
                        type="document",
                        content=normalized,
                        method="raw_text_cache",
                        ordinal=1,
                        source_name=_safe_source_name(source),
                    )
                )
            if progress_cb:
                progress_cb(1, 1)
            if diagnostics is not None:
                diagnostics.append(
                    {
                        "method": "raw_text_cache",
                        "status": "success",
                        "chars": len(raw_text),
                        "adapter": self.name,
                        "normalized": normalized != raw_text,
                    }
                )
            return DocumentParserAdapterResult(
                text=normalized,
                from_cache=True,
                normalized=normalized != raw_text,
                should_persist=normalized != raw_text,
            )

        segment_start = len(segment_collector) if segment_collector is not None else 0
        reader_kwargs = {
            "progress_cb": progress_cb,
            "diagnostics": diagnostics,
        }
        if segment_collector is not None:
            reader_kwargs["segment_collector"] = segment_collector
        text = self.content_reader(source, **reader_kwargs)
        if not text:
            return DocumentParserAdapterResult(text=None)

        normalized = self.normalizer(text)
        if segment_collector is not None:
            new_segments = segment_collector[segment_start:]
            for segment in new_segments:
                segment.content = self.normalizer(segment.content or "")
            if not new_segments:
                method = next(
                    (
                        item.get("method")
                        for item in reversed(diagnostics or [])
                        if isinstance(item, dict) and item.get("status") == "success"
                    ),
                    None,
                )
                segment_collector.append(
                    DocumentParseSegment(
                        type="document",
                        content=normalized,
                        method=method,
                        ordinal=1,
                        source_name=_safe_source_name(source),
                    )
                )
        return DocumentParserAdapterResult(
            text=normalized,
            from_cache=False,
            normalized=normalized != text,
            should_persist=True,
        )


def _safe_source_name(source: Any) -> str | None:
    storage_path = getattr(source, "storage_path", None)
    if storage_path:
        return Path(str(storage_path)).name
    title = getattr(source, "title", None)
    return str(title).strip() if title else None


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _tesseract_engine_available() -> bool:
    return shutil.which("tesseract") is not None


def resolve_document_parser_adapter(
    name: str | None,
    *,
    content_reader: ContentReader,
    normalizer: TextNormalizer,
) -> CurrentDocumentParserAdapter:
    requested = (name or "current").strip().lower()
    if requested != "current":
        logger.warning(
            f"[DocumentParser] adapter '{requested}' is not available; falling back to current parser"
        )
    return CurrentDocumentParserAdapter(
        content_reader=content_reader,
        normalizer=normalizer,
        requested_name=requested or "current",
    )


def get_document_parser_capabilities(name: str | None) -> dict[str, Any]:
    requested = (name or "current").strip().lower() or "current"
    active = "current"
    is_fallback = requested != active
    return {
        "requested_adapter": requested,
        "active_adapter": active,
        "status": "fallback" if is_fallback else "ready",
        "input_modes": [
            {"kind": "raw_text", "status": "supported"},
            {"kind": "txt_md", "status": "supported"},
            {"kind": "docx", "status": "supported"},
            {
                "kind": "pdf",
                "status": "best_effort",
                "notes": ["native text extraction first; OCR behavior depends on runtime dependencies"],
            },
            {
                "kind": "image",
                "status": "best_effort",
                "notes": ["vision/OCR quality depends on configured model and local OCR availability"],
            },
            {
                "kind": "multi_image_chat",
                "status": "best_effort",
                "notes": ["chat screenshot merging is supported but still needs representative regression data"],
            },
        ],
        "features": {
            "document_parse_summary": True,
            "method_attempt_diagnostics": True,
            "progress_callback": True,
            "cjk_normalization": True,
            "page_level_manifest": True,
            "page_level_hybrid_ocr": True,
            "image_slice_diagnostics": True,
            "page_level_spans": False,
            "table_structure": False,
            "ocr_confidence": False,
            "layout_bounding_boxes": False,
        },
        "runtime_dependencies": {
            "pdfium_binding": _module_available("pypdfium2"),
            "pdfplumber_binding": _module_available("pdfplumber"),
            "pillow_binding": _module_available("PIL"),
            "pytesseract_binding": _module_available("pytesseract"),
            "tesseract_engine": _tesseract_engine_available(),
        },
        "notes": (
            [f"requested adapter '{requested}' is unavailable; falling back to current parser"]
            if is_fallback
            else ["current parser adapter active"]
        ),
    }
