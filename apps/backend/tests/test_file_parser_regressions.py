from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from app.services import file_parser
from app.utils.document_parse_result import DocumentParseSegment, DocumentParseResult


def _write_full_page_image_pdf(
    pdf_path,
    tmp_path,
    *,
    page_count: int,
    image_pages: set[int],
    text_by_page: dict[int, str] | None = None,
):
    from pdf_fixture import builder as fitz
    Image = pytest.importorskip("PIL.Image")

    image_path = tmp_path / f"{pdf_path.stem}-full-page.png"
    Image.new("RGB", (600, 800), "white").save(image_path)
    image_bytes = image_path.read_bytes()
    text_by_page = text_by_page or {}

    doc = fitz.open()
    try:
        for page_number in range(1, page_count + 1):
            page = doc.new_page(width=600, height=800)
            if page_number in image_pages:
                page.insert_image(page.rect, stream=image_bytes)
            elif page_number in text_by_page:
                page.insert_text((72, 72), text_by_page[page_number], fontsize=12)
        doc.save(str(pdf_path))
    finally:
        doc.close()


def test_pdf_text_layer_extraction_reports_page_progress(tmp_path):
    from pdf_fixture import builder as fitz

    pdf_path = tmp_path / "sample-text-layer.pdf"
    doc = fitz.open()
    try:
        for text in ("Page 1: oil inventories fell and risk appetite improved.", "Page 2: the dollar index weakened while oil stayed range-bound."):
            page = doc.new_page()
            page.insert_text((72, 72), text, fontsize=12)
        doc.save(str(pdf_path))
    finally:
        doc.close()

    progress = []
    diagnostics = []
    extracted = file_parser.extract_text(
        str(pdf_path),
        mime_type="application/pdf",
        progress_cb=lambda current, total: progress.append((current, total)),
        diagnostics=diagnostics,
    )

    assert extracted is not None
    assert "oil inventories fell" in extracted
    assert "dollar index weakened" in extracted
    assert progress[-1] == (2, 2)
    assert diagnostics[0]["method"] == "pdfium_text"
    assert diagnostics[0]["status"] == "success"
    assert diagnostics[0]["chars"] == len(extracted)


def test_pdf_text_layer_extraction_collects_ordered_page_segments(tmp_path):
    from pdf_fixture import builder as fitz

    pdf_path = tmp_path / "page-segments.pdf"
    doc = fitz.open()
    try:
        for text in ("First page anchor", "Second page anchor", "Third page anchor"):
            page = doc.new_page()
            page.insert_text((72, 72), text, fontsize=12)
        doc.save(str(pdf_path))
    finally:
        doc.close()

    segments: list[DocumentParseSegment] = []
    extracted = file_parser.extract_text(
        str(pdf_path),
        mime_type="application/pdf",
        segment_collector=segments,
    )

    assert extracted == "First page anchor\n\nSecond page anchor\n\nThird page anchor"
    assert [segment.page for segment in segments] == [1, 2, 3]
    assert [segment.ordinal for segment in segments] == [1, 2, 3]
    assert [segment.content for segment in segments] == [
        "First page anchor",
        "Second page anchor",
        "Third page anchor",
    ]
    assert all(segment.method == "pdfium_text" for segment in segments)
    assert all(segment.source_name == "page-segments.pdf" for segment in segments)


def test_mixed_pdf_ocrs_only_visual_page_and_preserves_source_order(tmp_path, monkeypatch):
    pdf_path = tmp_path / "text-scan-text.pdf"
    _write_full_page_image_pdf(
        pdf_path,
        tmp_path,
        page_count=3,
        image_pages={2},
        text_by_page={1: "Native first page", 3: "Native third page"},
    )

    from app.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_MIN_VISUAL_COVERAGE", 0.15, raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_MIN_DRAWING_COUNT", 50, raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_SKIP_DECORATIVE_EDGE_PAIR", True, raising=False)
    monkeypatch.setattr(file_parser, "_build_openrouter_vision_client", lambda _settings: object())
    monkeypatch.setattr(file_parser, "_render_pdf_page_image", lambda page, _settings: page.number + 1)
    called_pages = []

    def fake_page_ocr(_image, _settings, openrouter_client=None, page_number=None, **_kwargs):
        assert openrouter_client is not None
        called_pages.append(page_number)
        return "OCR middle page"

    monkeypatch.setattr(file_parser, "_ocr_pdf_page_image", fake_page_ocr)
    diagnostics = []
    segments = []
    progress = []

    extracted = file_parser.extract_text(
        str(pdf_path),
        mime_type="application/pdf",
        progress_cb=lambda current, total: progress.append((current, total)),
        diagnostics=diagnostics,
        segment_collector=segments,
    )

    assert extracted == "Native first page\n\nOCR middle page\n\nNative third page"
    assert called_pages == [2]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert [segment.page for segment in segments] == [1, 2, 3]
    assert [segment.method for segment in segments] == ["pdfium_text", "pdf_ocr", "pdfium_text"]
    assert diagnostics[-1]["method"] == "pdf_hybrid"
    assert diagnostics[-1]["status"] == "success"
    assert diagnostics[-1]["ocr_pages"] == [2]


def test_mixed_pdf_failed_visual_page_is_partial(tmp_path, monkeypatch):
    pdf_path = tmp_path / "text-failed-scan-text.pdf"
    _write_full_page_image_pdf(
        pdf_path,
        tmp_path,
        page_count=3,
        image_pages={2},
        text_by_page={1: "Native first page", 3: "Native third page"},
    )

    from app.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_MIN_VISUAL_COVERAGE", 0.15, raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_MIN_DRAWING_COUNT", 50, raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_SKIP_DECORATIVE_EDGE_PAIR", True, raising=False)
    monkeypatch.setattr(file_parser, "_build_openrouter_vision_client", lambda _settings: object())
    monkeypatch.setattr(file_parser, "_render_pdf_page_image", lambda page, _settings: page.number + 1)
    monkeypatch.setattr(file_parser, "_ocr_pdf_page_image", lambda *_args, **_kwargs: None)
    diagnostics = []
    segments = []

    extracted = file_parser.extract_text(
        str(pdf_path),
        mime_type="application/pdf",
        diagnostics=diagnostics,
        segment_collector=segments,
    )
    result = DocumentParseResult.from_text(
        extracted or "",
        source_type="document",
        method_attempts=diagnostics,
        segments=segments,
    )

    assert extracted is not None
    assert [segment.page for segment in segments] == [1, 2, 3]
    assert segments[1].content == ""
    assert diagnostics[-1]["method"] == "pdf_hybrid"
    assert diagnostics[-1]["status"] == "partial"
    assert diagnostics[-1]["failed_pages"] == [2]
    assert result.status == "partial"
    assert result.pages_failed == [2]


def test_long_pdf_decorative_edge_pair_is_explicitly_skipped(tmp_path, monkeypatch):
    pdf_path = tmp_path / "long-report-with-cover-and-back.pdf"
    _write_full_page_image_pdf(
        pdf_path,
        tmp_path,
        page_count=20,
        image_pages={1, 20},
        text_by_page={page: f"Native report page {page}" for page in range(2, 20)},
    )

    from app.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_MIN_VISUAL_COVERAGE", 0.15, raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_MIN_DRAWING_COUNT", 50, raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_HYBRID_SKIP_DECORATIVE_EDGE_PAIR", True, raising=False)

    def unexpected_ocr(*_args, **_kwargs):
        raise AssertionError("decorative edge pages must not call OCR")

    monkeypatch.setattr(file_parser, "_ocr_pdf_page_image", unexpected_ocr)
    diagnostics = []
    segments = []
    progress = []

    extracted = file_parser.extract_text(
        str(pdf_path),
        mime_type="application/pdf",
        progress_cb=lambda current, total: progress.append((current, total)),
        diagnostics=diagnostics,
        segment_collector=segments,
    )

    assert extracted is not None
    assert "Native report page 2" in extracted
    assert "Native report page 19" in extracted
    assert len(segments) == 20
    assert [segment.page for segment in segments] == list(range(1, 21))
    assert segments[0].method == "pdfium_decorative_skip"
    assert segments[-1].method == "pdfium_decorative_skip"
    assert progress == [(page, 20) for page in range(1, 21)]
    assert diagnostics[-1]["method"] == "pdfium_text"
    assert diagnostics[-1]["status"] == "success"
    assert diagnostics[-1]["skipped_decorative_pages"] == [1, 20]
    assert diagnostics[-1].get("model_calls") in (None, [])


def test_image_ocr_falls_back_to_tesseract_when_vision_unavailable(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")

    image_path = tmp_path / "ocr-fallback.png"
    Image.new("RGB", (160, 80), "white").save(image_path)

    monkeypatch.setattr(
        file_parser,
        "_extract_image_vision",
        lambda _path, progress_cb=None, details=None: None,
    )
    monkeypatch.setattr(
        file_parser,
        "_extract_image_openrouter",
        lambda _path, vision_prompt="", progress_cb=None, details=None: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "pytesseract",
        SimpleNamespace(
            pytesseract=SimpleNamespace(tesseract_cmd=sys.executable),
            image_to_string=lambda _img, lang="": "OCR fallback text",
        ),
    )

    diagnostics = []
    extracted = file_parser.extract_text(str(image_path), mime_type="image/png", diagnostics=diagnostics)

    assert extracted == "OCR fallback text"
    assert [item["method"] for item in diagnostics] == [
        "openai_vision",
        "openrouter_vision",
        "tesseract_ocr",
    ]
    assert diagnostics[-1]["status"] == "success"
    assert diagnostics[-1]["chars"] == len(extracted)


def test_openrouter_long_image_is_bounded_ordered_and_reports_failed_slices(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")

    image_path = tmp_path / "long-image.png"
    Image.new("RGB", (640, 3800), "white").save(image_path)

    from app.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-openrouter-key", raising=False)
    monkeypatch.setattr(settings, "PRECHECK_VISION_MODEL", "google/gemini-3-flash-preview", raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_OCR_MAX_WORKERS", 3, raising=False)
    fake_slices = [Image.new("RGB", (50, 50), (idx, 0, 0)) for idx in (1, 2, 3)]
    monkeypatch.setattr(file_parser, "_slice_image", lambda *_args, **_kwargs: fake_slices)
    monkeypatch.setattr(file_parser, "_build_openrouter_vision_client", lambda _settings: object())

    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_vision(img, _settings, _prompt="", client=None, **_kwargs):
        nonlocal active, max_active
        assert client is not None
        ordinal = img.getpixel((0, 0))[0]
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep({1: 0.04, 2: 0.01, 3: 0.02}[ordinal])
            return None if ordinal == 2 else f"第{ordinal}段内容"
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(file_parser, "_call_openrouter_vision_api", fake_vision)
    progress = []
    details = {}

    extracted = file_parser._extract_image_openrouter(
        image_path,
        progress_cb=lambda current, total: progress.append((current, total)),
        details=details,
    )

    assert extracted is not None
    assert extracted.index("第1段内容") < extracted.index("第3段内容")
    assert "第 2 段提取失败" in extracted
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert details["slices"] == 3
    assert details["completed_slices"] == 2
    assert details["failed_slices"] == [2]
    assert details["max_workers"] == 3
    assert max_active == 3


def test_extract_image_records_partial_slice_diagnostics(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "partial-image.png"
    Image.new("RGB", (640, 3800), "white").save(image_path)

    monkeypatch.setattr(
        file_parser,
        "_extract_image_vision",
        lambda _path, progress_cb=None, details=None: None,
    )

    def fake_openrouter(_path, vision_prompt="", progress_cb=None, details=None):
        details.update(
            {
                "width": 640,
                "height": 3800,
                "slices": 3,
                "completed_slices": 2,
                "failed_slices": [2],
                "max_workers": 3,
            }
        )
        return "first\n\nthird"

    monkeypatch.setattr(file_parser, "_extract_image_openrouter", fake_openrouter)
    diagnostics = []

    extracted = file_parser.extract_text(
        str(image_path),
        mime_type="image/png",
        diagnostics=diagnostics,
    )

    assert extracted == "first\n\nthird"
    assert diagnostics[1]["method"] == "openrouter_vision"
    assert diagnostics[1]["status"] == "partial"
    assert diagnostics[1]["failed_slices"] == [2]
    assert diagnostics[1]["slices"] == 3


def test_openrouter_vision_call_records_safe_route_and_cost_trace(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    from app.config import settings

    monkeypatch.setattr(settings, "PRECHECK_VISION_MODEL", "google/gemini-3.1-flash-lite")
    monkeypatch.setattr(settings, "PRECHECK_VISION_FALLBACK", "google/gemini-3.5-flash")

    response = SimpleNamespace(
        id="gen-safe-123",
        model="google/gemini-3.1-flash-lite",
        provider="Google AI Studio",
        usage=SimpleNamespace(cost=0.00125),
        choices=[SimpleNamespace(message=SimpleNamespace(content="verified OCR text"))],
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: response),
        )
    )
    traces = []

    result = file_parser._call_openrouter_vision_api(
        Image.new("RGB", (120, 80), "white"),
        settings,
        client=fake_client,
        trace_collector=traces,
        trace_context={"unit_type": "image_slice", "slice": 2},
    )

    assert result == "verified OCR text"
    assert traces == [
        {
            "unit_type": "image_slice",
            "slice": 2,
            "attempt": 1,
            "status": "success",
            "requested_model": "google/gemini-3.1-flash-lite",
            "actual_model": "google/gemini-3.1-flash-lite",
            "provider": "Google AI Studio",
            "reasoning_effort": "none",
            "duration_ms": pytest.approx(traces[0]["duration_ms"], abs=1),
            "reported_cost_usd": 0.00125,
        }
    ]
    assert "gen-safe-123" not in str(traces)


def test_openrouter_vision_error_trace_exposes_type_not_provider_message(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    from app.config import settings

    monkeypatch.setattr(settings, "PRECHECK_VISION_MODEL", "google/gemini-3.1-flash-lite")
    monkeypatch.setattr(settings, "PRECHECK_VISION_FALLBACK", "")

    def fail(**_kwargs):
        raise RuntimeError("sensitive upstream payload")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fail)),
    )
    traces = []

    result = file_parser._call_openrouter_vision_api(
        Image.new("RGB", (120, 80), "white"),
        settings,
        client=fake_client,
        trace_collector=traces,
    )

    assert result is None
    assert traces[0]["status"] == "error"
    assert traces[0]["error_type"] == "RuntimeError"
    assert "sensitive upstream payload" not in str(traces)


def test_openrouter_vision_rejects_truncation_and_uses_fallback(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    from app.config import settings

    monkeypatch.setattr(settings, "PRECHECK_VISION_MODEL", "primary-vision")
    monkeypatch.setattr(settings, "PRECHECK_VISION_FALLBACK", "fallback-vision")
    monkeypatch.setattr(settings, "DOCUMENT_OCR_MAX_TOKENS", 8000)
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if kwargs["model"] == "primary-vision":
            return SimpleNamespace(
                model="primary-vision",
                provider="provider-a",
                usage=SimpleNamespace(cost=0.001),
                choices=[
                    SimpleNamespace(
                        finish_reason="length",
                        message=SimpleNamespace(content="truncated but non-empty text"),
                    )
                ],
            )
        return SimpleNamespace(
            model="fallback-vision",
            provider="provider-b",
            usage=SimpleNamespace(cost=0.002),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="complete fallback transcription"),
                )
            ],
        )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    traces = []

    result = file_parser._call_openrouter_vision_api(
        Image.new("RGB", (120, 80), "white"),
        settings,
        client=fake_client,
        trace_collector=traces,
    )

    assert result == "complete fallback transcription"
    assert [item["status"] for item in traces] == ["truncated", "success"]
    assert [item["finish_reason"] for item in traces] == ["length", "stop"]
    assert [request["model"] for request in requests] == ["primary-vision", "fallback-vision"]
    assert all(request["max_tokens"] == 8000 for request in requests)
    assert all(request["temperature"] == 0 for request in requests)
    assert all(request["seed"] == 0 for request in requests)
    assert requests[0]["extra_body"] == {
        "reasoning": {"effort": "none", "exclude": True}
    }
    assert requests[1]["extra_body"] == {
        "reasoning": {"effort": "minimal", "exclude": True}
    }


def test_pdf_ocr_is_bounded_ordered_and_collects_failed_page(tmp_path, monkeypatch):
    from pdf_fixture import builder as fitz
    pdf_path = tmp_path / "scan.pdf"
    doc = fitz.open()
    try:
        for _ in range(4):
            doc.new_page()
        doc.save(str(pdf_path))
    finally:
        doc.close()

    from app.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_OCR_MAX_WORKERS", 2, raising=False)
    monkeypatch.setattr(file_parser, "_build_openrouter_vision_client", lambda _settings: object())
    monkeypatch.setattr(
        file_parser,
        "_render_pdf_page_image",
        lambda page, _settings: page.number + 1,
    )
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_page_ocr(_image, _settings, openrouter_client=None, page_number=None, **_kwargs):
        nonlocal active, max_active
        assert openrouter_client is not None
        assert page_number is not None
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep({1: 0.04, 2: 0.01, 3: 0.03, 4: 0.01}[page_number])
            return None if page_number == 2 else f"page-{page_number}"
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(file_parser, "_ocr_pdf_page_image", fake_page_ocr)
    diagnostics = []
    segments = []
    progress = []

    extracted = file_parser._pdf_ocr(
        pdf_path,
        progress_cb=lambda current, total: progress.append((current, total)),
        diagnostics=diagnostics,
        segment_collector=segments,
    )

    assert extracted.index("page-1") < extracted.index("page-3") < extracted.index("page-4")
    assert "第 2 页提取失败" in extracted
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert max_active == 2
    assert [segment.page for segment in segments] == [1, 2, 3, 4]
    assert segments[1].content == ""
    assert diagnostics[-1]["status"] == "partial"
    assert diagnostics[-1]["failed_pages"] == [2]


def test_pdf_page_ocr_uses_quality_first_model_route(monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    from app.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_PDF_OCR_MODEL", "quality-pdf-model", raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_PDF_OCR_FALLBACK_MODEL", "economy-pdf-model", raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_PDF_OCR_REASONING_EFFORT", "minimal", raising=False)
    monkeypatch.setattr(settings, "DOCUMENT_PDF_OCR_FALLBACK_REASONING_EFFORT", "none", raising=False)
    captured = {}

    def fake_openrouter(_image, _settings, _prompt="", **kwargs):
        captured.update(kwargs)
        return "complete PDF page"

    monkeypatch.setattr(file_parser, "_call_openrouter_vision_api", fake_openrouter)

    result = file_parser._ocr_pdf_page_image(
        Image.new("RGB", (100, 100), "white"),
        settings,
        openrouter_client=object(),
        page_number=4,
    )

    assert result["text"] == "complete PDF page"
    assert captured["primary_model"] == "quality-pdf-model"
    assert captured["fallback_model"] == "economy-pdf-model"
    assert captured["primary_reasoning_effort"] == "minimal"
    assert captured["fallback_reasoning_effort"] == "none"


def test_scan_pdf_native_probe_does_not_reset_ocr_progress(tmp_path, monkeypatch):
    from pdf_fixture import builder as fitz
    pdf_path = tmp_path / "two-page-scan.pdf"
    doc = fitz.open()
    try:
        doc.new_page()
        doc.new_page()
        doc.save(str(pdf_path))
    finally:
        doc.close()

    def fake_pdf_ocr(
        _path,
        progress_cb=None,
        diagnostics=None,
        segment_collector=None,
    ):
        progress_cb(1, 2)
        progress_cb(2, 2)
        diagnostics.append({"method": "pdf_ocr", "status": "success", "pages": 2, "chars": 8})
        return "ocr text"

    monkeypatch.setattr(file_parser, "_pdf_ocr", fake_pdf_ocr)
    progress = []

    extracted = file_parser.extract_text(
        str(pdf_path),
        mime_type="application/pdf",
        progress_cb=lambda current, total: progress.append((current, total)),
        diagnostics=[],
    )

    assert extracted == "ocr text"
    assert progress == [(1, 2), (2, 2)]


def test_extract_text_records_unsupported_format_diagnostics(tmp_path):
    unsupported = tmp_path / "unsupported.xyz"
    unsupported.write_text("not supported", encoding="utf-8")
    diagnostics = []

    extracted = file_parser.extract_text(str(unsupported), diagnostics=diagnostics)

    assert extracted is None
    assert diagnostics == [
        {
            "method": "dispatch",
            "status": "failed",
            "reason_code": "unsupported_format",
            "file_type": ".xyz",
        }
    ]
