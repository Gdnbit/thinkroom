"""Non-secret identity for the active document parsing route."""
from __future__ import annotations

from typing import Any

from app.config import Settings, settings
from app.services.document_parser_adapter import get_document_parser_capabilities
from app.services.route_identity_common import build_identity


DOCUMENT_PARSER_CONTRACT_VERSION = "ingestion-quality-v2"
DOCUMENT_PARSER_IMPLEMENTATION_FILES = (
    "services/file_parser.py",
    "services/pdf_backend.py",
    "services/document_parser_adapter.py",
    "services/document_parser_identity.py",
    "utils/document_parse_result.py",
    "utils/parse_diagnostics.py",
    "workflows/ingestion_workflow.py",
)


def get_document_parser_identity(cfg: Settings = settings) -> dict[str, Any]:
    capabilities = get_document_parser_capabilities(cfg.DOCUMENT_PARSER_ADAPTER)
    configuration = {
        "requested_adapter": str(cfg.DOCUMENT_PARSER_ADAPTER or "current"),
        "active_adapter": capabilities["active_adapter"],
        "ocr_max_workers": max(1, int(cfg.DOCUMENT_OCR_MAX_WORKERS or 1)),
        "ocr_page_dpi": max(72, int(cfg.DOCUMENT_OCR_PAGE_DPI or 72)),
        "ocr_page_slice_threshold": max(1, int(cfg.DOCUMENT_OCR_PAGE_SLICE_THRESHOLD or 1)),
        "ocr_timeout_seconds": max(1.0, float(cfg.DOCUMENT_OCR_REQUEST_TIMEOUT_SECONDS or 1.0)),
        "ocr_max_retries": max(0, int(cfg.DOCUMENT_OCR_MAX_RETRIES or 0)),
        "ocr_max_tokens": max(1, int(cfg.DOCUMENT_OCR_MAX_TOKENS or 1)),
        "ocr_reasoning_effort": str(cfg.DOCUMENT_OCR_REASONING_EFFORT or "none"),
        "ocr_fallback_reasoning_effort": str(
            cfg.DOCUMENT_OCR_FALLBACK_REASONING_EFFORT or "minimal"
        ),
        "pdf_ocr_primary_model": str(cfg.DOCUMENT_PDF_OCR_MODEL or "") or None,
        "pdf_ocr_fallback_model": str(cfg.DOCUMENT_PDF_OCR_FALLBACK_MODEL or "") or None,
        "pdf_ocr_reasoning_effort": str(cfg.DOCUMENT_PDF_OCR_REASONING_EFFORT or "minimal"),
        "pdf_ocr_fallback_reasoning_effort": str(
            cfg.DOCUMENT_PDF_OCR_FALLBACK_REASONING_EFFORT or "none"
        ),
        "hybrid_min_visual_coverage": min(
            1.0,
            max(0.0, float(cfg.DOCUMENT_HYBRID_MIN_VISUAL_COVERAGE or 0.0)),
        ),
        "hybrid_min_drawing_count": max(0, int(cfg.DOCUMENT_HYBRID_MIN_DRAWING_COUNT or 0)),
        "hybrid_skip_decorative_edge_pair": bool(cfg.DOCUMENT_HYBRID_SKIP_DECORATIVE_EDGE_PAIR),
        "vision_primary_model": str(cfg.PRECHECK_VISION_MODEL or "") or None,
        "vision_fallback_model": str(cfg.PRECHECK_VISION_FALLBACK or "") or None,
        "runtime_dependencies": dict(capabilities["runtime_dependencies"]),
        "features": dict(capabilities["features"]),
    }
    return build_identity(
        scope="document_parser",
        contract_version=DOCUMENT_PARSER_CONTRACT_VERSION,
        configuration=configuration,
        implementation_files=DOCUMENT_PARSER_IMPLEMENTATION_FILES,
    )


__all__ = ["DOCUMENT_PARSER_CONTRACT_VERSION", "get_document_parser_identity"]
