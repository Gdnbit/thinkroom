from types import SimpleNamespace

from app.services.document_parser_adapter import (
    CurrentDocumentParserAdapter,
    get_document_parser_capabilities,
    resolve_document_parser_adapter,
)
from app.utils.document_parse_result import DocumentParseSegment


def test_current_document_parser_adapter_normalizes_raw_text_cache():
    source = SimpleNamespace(raw_text="ＡＢＣ", source_type="article", storage_path=None)
    diagnostics = []
    progress_events = []

    def fail_reader(*_args, **_kwargs):
        raise AssertionError("cached raw_text should not read file content")

    adapter = CurrentDocumentParserAdapter(
        content_reader=fail_reader,
        normalizer=lambda text: text.translate(str.maketrans({"Ａ": "A", "Ｂ": "B", "Ｃ": "C"})),
    )

    result = adapter.parse(
        source,
        progress_cb=lambda current, total: progress_events.append((current, total)),
        diagnostics=diagnostics,
    )

    assert result.text == "ABC"
    assert result.from_cache is True
    assert result.normalized is True
    assert result.should_persist is True
    assert progress_events == [(1, 1)]
    assert diagnostics[0]["method"] == "raw_text_cache"
    assert diagnostics[0]["adapter"] == "current"
    assert diagnostics[0]["normalized"] is True


def test_current_document_parser_adapter_reads_content_and_preserves_attempts():
    source = SimpleNamespace(raw_text="", source_type="article", storage_path="fixture.txt")
    diagnostics = []

    def reader(source_obj, progress_cb=None, diagnostics=None):
        assert source_obj is source
        assert progress_cb is None
        diagnostics.append({"method": "read_text", "status": "success", "chars": 5})
        return "hello"

    adapter = CurrentDocumentParserAdapter(content_reader=reader, normalizer=lambda text: text.upper())

    result = adapter.parse(source, diagnostics=diagnostics)

    assert result.text == "HELLO"
    assert result.from_cache is False
    assert result.normalized is True
    assert result.should_persist is True
    assert diagnostics == [{"method": "read_text", "status": "success", "chars": 5}]


def test_resolve_document_parser_adapter_falls_back_to_current_for_unknown_name():
    adapter = resolve_document_parser_adapter(
        "docling",
        content_reader=lambda *_args, **_kwargs: "text",
        normalizer=lambda text: text,
    )

    assert isinstance(adapter, CurrentDocumentParserAdapter)
    assert adapter.name == "current"
    assert adapter.requested_name == "docling"


def test_current_document_parser_adapter_normalizes_collected_segments():
    source = SimpleNamespace(raw_text="", source_type="report", storage_path="fixture.pdf")
    segments = []

    def reader(source_obj, progress_cb=None, diagnostics=None, segment_collector=None):
        assert source_obj is source
        segment_collector.extend([
            DocumentParseSegment(
                type="document",
                content="Ｐａｇｅ one",
                method="pdfium_text",
                page=1,
                source_name="fixture.pdf",
            ),
        ])
        return "Ｐａｇｅ one"

    adapter = CurrentDocumentParserAdapter(
        content_reader=reader,
        normalizer=lambda text: text.replace("Ｐａｇｅ", "Page"),
    )

    result = adapter.parse(source, segment_collector=segments)

    assert result.text == "Page one"
    assert result.normalized is True
    assert len(segments) == 1
    assert segments[0].content == "Page one"
    assert segments[0].page == 1


def test_current_document_parser_adapter_creates_cache_segment_when_requested():
    source = SimpleNamespace(raw_text="cached text", source_type="report", storage_path="fixture.pdf")
    segments = []
    adapter = CurrentDocumentParserAdapter(
        content_reader=lambda *_args, **_kwargs: "unreachable",
        normalizer=lambda text: text,
    )

    result = adapter.parse(source, segment_collector=segments)

    assert result.from_cache is True
    assert len(segments) == 1
    assert segments[0].method == "raw_text_cache"
    assert segments[0].source_name == "fixture.pdf"


def test_document_parser_capabilities_distinguish_binding_from_ocr_engine(monkeypatch):
    from app.services import document_parser_adapter

    monkeypatch.setattr(
        document_parser_adapter,
        "_module_available",
        lambda name: name in {"pypdfium2", "pdfplumber", "PIL", "pytesseract"},
    )
    monkeypatch.setattr(document_parser_adapter, "_tesseract_engine_available", lambda: False)

    capabilities = get_document_parser_capabilities("current")

    assert capabilities["features"]["page_level_manifest"] is True
    assert capabilities["features"]["page_level_spans"] is False
    assert capabilities["runtime_dependencies"]["pytesseract_binding"] is True
    assert capabilities["runtime_dependencies"]["tesseract_engine"] is False
    assert capabilities["input_modes"][3]["status"] == "best_effort"
