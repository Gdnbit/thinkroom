from __future__ import annotations

from app.utils.document_parse_result import DocumentParseResult, DocumentParseSegment


def test_document_parse_result_summarizes_text_without_storing_full_content():
    text = "第一段用于验证解析结果。\n\n第二段用于验证片段摘要。" * 20
    result = DocumentParseResult.from_text(
        text,
        source_type="report",
        method_attempts=[
            {"method": "pdfium_text", "status": "success", "chars": len(text), "pages": 3},
        ],
    )

    summary = result.to_summary(segment_excerpt_chars=30)

    assert summary["status"] == "completed"
    assert summary["text_chars"] == len(text)
    assert summary["segment_count"] == 1
    assert summary["methods"] == ["pdfium_text"]
    assert summary["pages_total"] == 3
    assert summary["segments"][0]["type"] == "document"
    assert summary["segments"][0]["method"] == "pdfium_text"
    assert summary["segments"][0]["chars"] == len(text)
    assert len(summary["segments"][0]["excerpt"]) <= 30
    assert text not in str(summary)


def test_document_parse_result_tracks_partial_page_failures():
    result = DocumentParseResult.from_text(
        "OCR text",
        source_type="image",
        method_attempts=[
            {"method": "pdf_ocr", "status": "success", "pages": 5, "failed_pages": [2, 4]},
            {"method": "openrouter_vision", "status": "empty", "reason_code": "no_text_or_unavailable"},
        ],
    )

    summary = result.to_summary()

    assert summary["status"] == "partial"
    assert summary["methods"] == ["pdf_ocr", "openrouter_vision"]
    assert summary["pages_total"] == 5
    assert summary["pages_failed"] == [2, 4]
    assert "partial_parse" in summary["warnings"]


def test_document_parse_result_aligns_page_segments_and_persists_bounded_manifest():
    page_one = "Page one contains the first verified fact."
    page_two = "第二页包含可验证的中文事实。"
    text = f"{page_one}\n\n{page_two}"
    result = DocumentParseResult.from_text(
        text,
        source_type="report",
        method_attempts=[
            {"method": "pdfium_text", "status": "success", "chars": len(text), "pages": 2},
        ],
        segments=[
            DocumentParseSegment(
                type="document",
                content=page_one,
                method="pdfium_text",
                page=1,
                ordinal=1,
                source_name="fixture.pdf",
            ),
            DocumentParseSegment(
                type="document",
                content=page_two,
                method="pdfium_text",
                page=2,
                ordinal=2,
                source_name="fixture.pdf",
            ),
        ],
    )

    summary = result.to_summary(segment_excerpt_chars=12)

    assert summary["segment_count"] == 2
    assert summary["page_segment_count"] == 2
    assert summary["segment_manifest_truncated"] is False
    assert len(summary["segment_manifest"]) == 2
    assert summary["segment_manifest"][0]["page"] == 1
    assert summary["segment_manifest"][0]["ordinal"] == 1
    assert summary["segment_manifest"][0]["source_name"] == "fixture.pdf"
    assert summary["segment_manifest"][0]["char_start"] == 0
    assert summary["segment_manifest"][0]["char_end"] == len(page_one)
    assert summary["segment_manifest"][1]["char_start"] == len(page_one) + 2
    assert summary["segment_manifest"][1]["char_end"] == len(text)
    assert summary["segment_manifest"][0]["text_hash"].startswith("sha256:")
    assert page_one not in str(summary["segment_manifest"])


def test_document_parse_result_marks_unaligned_segments_without_hiding_them():
    result = DocumentParseResult.from_text(
        "stored text",
        source_type="report",
        segments=[
            DocumentParseSegment(type="document", content="different text", page=1),
        ],
    )

    summary = result.to_summary()

    assert "segment_alignment_incomplete" in summary["warnings"]
    assert summary["segment_manifest"][0]["aligned"] is False
    assert "char_start" not in summary["segment_manifest"][0]


def test_document_parse_result_tracks_partial_image_slices():
    result = DocumentParseResult.from_text(
        "first\n\nthird",
        source_type="image",
        method_attempts=[
            {
                "method": "openrouter_vision",
                "status": "partial",
                "slices": 3,
                "failed_slices": [2],
            },
        ],
    )

    summary = result.to_summary()

    assert summary["status"] == "partial"
    assert summary["slices_total"] == 3
    assert summary["slices_failed"] == [2]
    assert "partial_parse" in summary["warnings"]
