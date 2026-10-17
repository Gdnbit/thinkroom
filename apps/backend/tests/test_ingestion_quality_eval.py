from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.document_parser_identity import get_document_parser_identity
from app.utils.document_parse_result import DocumentParseSegment
from app.verification import ingestion_quality_eval
from app.verification.ingestion_quality_eval import (
    default_suite_path,
    load_suite,
    main,
    normalize_anchor_text,
    run_suite,
    validate_suite,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_normalize_anchor_text_is_stable_for_ocr_spacing_and_punctuation():
    assert normalize_anchor_text("ＡＩ／量子（安防） 25% - 30%") == "ai量子安防2530"
    assert normalize_anchor_text("风险传导链条：从“影子银行”到「传统银行」") == "风险传导链条从影子银行到传统银行"


def test_validate_suite_rejects_path_escape(tmp_path):
    suite = {
        "schema_version": 1,
        "suite_id": "unsafe",
        "policy": {"max_external_calls": 0},
        "cases": [
            {
                "case_id": "escape",
                "execution_mode": "offline",
                "path": "../outside.txt",
                "sha256": "0" * 64,
                "bytes": 1,
                "expected": {},
                "anchors": [],
            }
        ],
    }

    with pytest.raises(ValueError, match="path must stay inside repository"):
        validate_suite(suite, repo_root=tmp_path)


def test_offline_ingestion_quality_suite_uses_real_parser_and_passes_without_external_calls():
    suite = load_suite(default_suite_path())

    report = run_suite(suite, mode="offline", repo_root=REPO_ROOT)

    assert report["status"] == "passed"
    assert report["case_count"] == 5
    assert report["passed_count"] == 5
    assert report["failed_count"] == 0
    assert report["external_call_count"] == 0
    assert report["parser_identity"]["fingerprint"].startswith("sha256:")
    assert report["parser_fingerprint"] == report["parser_identity"]["fingerprint"]

    by_id = {case["case_id"]: case for case in report["cases"]}
    long_pdf = by_id["IQ-PDF-TEXT-LONG-001"]
    assert long_pdf["status"] == "passed"
    assert long_pdf["observed"]["pages_total"] == 102
    assert long_pdf["observed"]["segment_count"] == 102
    assert long_pdf["observed"]["nonempty_page_segment_count"] == 100
    assert long_pdf["observed"]["method"] == "pdfium_text"
    assert long_pdf["observed"]["progress_monotonic"] is True
    assert all(anchor["matched"] for anchor in long_pdf["anchors"])
    assert "text" not in long_pdf["observed"]
    serialized = json.dumps(report, ensure_ascii=False)
    assert "while more intense rainfall can overwhelm outdated drainage systems" not in serialized
    assert '"excerpt"' not in serialized


def test_document_parser_identity_is_non_secret_and_configuration_bound():
    identity = get_document_parser_identity()
    serialized = json.dumps(identity, ensure_ascii=False).lower()

    assert identity["scope"] == "document_parser"
    assert identity["contract_version"] == "ingestion-quality-v2"
    assert identity["implementation_file_count"] >= 4
    assert identity["configuration"]["ocr_max_workers"] >= 1
    assert identity["configuration"]["vision_primary_model"] == "google/gemini-3.1-flash-lite"
    assert identity["configuration"]["vision_fallback_model"] == "google/gemini-3.5-flash"
    assert identity["configuration"]["pdf_ocr_primary_model"] == "google/gemini-3.5-flash"
    assert identity["configuration"]["pdf_ocr_fallback_model"] == "google/gemini-3.1-flash-lite"
    assert identity["configuration"]["features"]["page_level_hybrid_ocr"] is True
    assert "api_key" not in serialized
    assert "bearer " not in serialized


def test_online_cli_requires_explicit_external_call_authorization(tmp_path):
    report_path = tmp_path / "online-report.json"

    exit_code = main(
        [
            "--suite",
            str(default_suite_path()),
            "--mode",
            "online",
            "--report",
            str(report_path),
            "--fail-on-issue",
        ]
    )

    assert exit_code == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["issues"][0]["reason_code"] == "external_calls_not_authorized"
    assert "key" not in json.dumps(report).lower()


def test_online_report_uses_safe_actual_model_calls_and_reported_cost(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (120, 80), "white").save(image_path)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    suite = {
        "schema_version": 1,
        "suite_id": "safe-model-trace",
        "policy": {"max_external_calls": 1},
        "cases": [
            {
                "case_id": "safe-trace-image",
                "execution_mode": "online",
                "path": "sample.png",
                "sha256": digest,
                "bytes": image_path.stat().st_size,
                "expected": {
                    "method": "openrouter_vision",
                    "min_text_chars": 10,
                    "width": 120,
                    "height": 80,
                    "slices": 1,
                    "external_calls": 1,
                },
                "anchors": [{"text": "verified OCR"}],
            }
        ],
    }

    def fake_extract_text(_path, progress_cb=None, diagnostics=None, segment_collector=None):
        text = "verified OCR text"
        progress_cb(1, 1)
        diagnostics.append(
            {
                "method": "openrouter_vision",
                "status": "success",
                "slices": 1,
                "model_calls": [
                    {
                        "unit_type": "image_slice",
                        "slice": 1,
                        "attempt": 1,
                        "status": "success",
                        "requested_model": "google/gemini-3.1-flash-lite",
                        "actual_model": "google/gemini-3.1-flash-lite",
                        "provider": "Google AI Studio",
                        "duration_ms": 123,
                        "reported_cost_usd": 0.00125,
                        "prompt": "must-not-enter-report",
                    }
                ],
            }
        )
        segment_collector.append(
            DocumentParseSegment(
                type="image",
                content=text,
                method="openrouter_vision",
                source_name="sample.png",
                ordinal=1,
            )
        )
        return text

    monkeypatch.setattr(ingestion_quality_eval, "extract_text", fake_extract_text)

    report = run_suite(suite, mode="online", repo_root=tmp_path)

    assert report["status"] == "passed"
    assert report["external_call_count"] == 1
    assert report["reported_cost_usd"] == 0.00125
    case = report["cases"][0]
    assert case["external_call_count"] == 1
    assert case["reported_cost_usd"] == 0.00125
    assert case["observed"]["model_calls"] == [
        {
            "unit_type": "image_slice",
            "slice": 1,
            "attempt": 1,
            "status": "success",
            "requested_model": "google/gemini-3.1-flash-lite",
            "actual_model": "google/gemini-3.1-flash-lite",
            "provider": "Google AI Studio",
            "duration_ms": 123,
            "reported_cost_usd": 0.00125,
        }
    ]
    assert "must-not-enter-report" not in json.dumps(report, ensure_ascii=False)
