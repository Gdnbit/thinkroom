from __future__ import annotations

from datetime import datetime, timezone

from ulid import ULID

from app.schemas.job_run import JobRun
from app.schemas.raw_source import RawSource
from app.workflows.ingestion_workflow import IngestionWorkflow
from app.utils.document_parse_result import DocumentParseSegment


def test_ingestion_failure_exposes_structured_parse_diagnostics(client, db, tmp_path):
    unsupported_file = tmp_path / "unsupported.xyz"
    unsupported_file.write_bytes(b"not a supported parser input")
    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="report",
        title="unsupported.xyz",
        storage_path=str(unsupported_file),
        raw_text=None,
        status="pending",
        language="en",
        word_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()

    result = IngestionWorkflow(db).process_source(source.id)

    assert result["success"] is False
    assert result["error"] == "No text content"
    assert result["parse_diagnostics"]["stage"] == "parse"
    assert result["parse_diagnostics"]["reason_code"] == "no_text_content"
    assert result["parse_diagnostics"]["file_type"] == ".xyz"
    assert result["parse_diagnostics"]["retryable"] is False
    assert result["parse_diagnostics"]["method_attempts"] == [
        {
            "method": "dispatch",
            "status": "failed",
            "reason_code": "unsupported_format",
            "file_type": ".xyz",
        }
    ]

    job = db.query(JobRun).filter(JobRun.related_object_id == source.id).one()
    assert job.status == "failed"
    assert job.result_summary["parse_diagnostics"]["reason_code"] == "no_text_content"

    status_resp = client.get(f"/api/v1/ingestion/status/{source.id}")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["status"] == "failed"
    assert status["error"] == "No text content"
    assert status["parse_diagnostics"]["stage"] == "parse"
    assert status["parse_diagnostics"]["attempted_methods"] == []
    assert status["parse_diagnostics"]["method_attempts"][0]["reason_code"] == "unsupported_format"

    batch_resp = client.post("/api/v1/ingestion/status/batch", json=[source.id])
    assert batch_resp.status_code == 200
    batch_status = batch_resp.json()[0]
    assert batch_status["error"] == "No text content"
    assert batch_status["parse_diagnostics"]["reason_code"] == "no_text_content"


def test_successful_ingestion_exposes_parse_attempts_for_operator_trust(client, db, tmp_path):
    text_file = tmp_path / "operator-trust.txt"
    text_file.write_text(
        "Research OS parser observability fixture.\n"
        "The upload flow should show that the text parser read this file and stored it.\n"
        "This fixture is used only for ingestion observability validation.",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    source = RawSource(
        id=f"src_test_{ULID()}",
        source_type="report",
        title="operator-trust.txt",
        storage_path=str(text_file),
        raw_text=None,
        status="pending",
        language="en",
        word_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()

    result = IngestionWorkflow(db).process_source(source.id)

    assert result["success"] is True
    assert result["parse_diagnostics"]["status"] == "completed"
    assert result["parse_diagnostics"]["reason_code"] == "text_extracted"
    assert result["parse_diagnostics"]["method_attempts"] == [
        {
            "method": "read_text",
            "status": "success",
            "chars": len(text_file.read_text(encoding="utf-8")),
        }
    ]
    assert result["parse_diagnostics"]["document_parse"]["status"] == "completed"
    assert result["parse_diagnostics"]["document_parse"]["text_chars"] == len(text_file.read_text(encoding="utf-8"))
    assert result["parse_diagnostics"]["document_parse"]["methods"] == ["read_text"]
    assert result["parse_diagnostics"]["document_parse"]["segments"][0]["type"] == "document"

    job = db.query(JobRun).filter(JobRun.related_object_id == source.id).one()
    assert job.result_summary["parse_diagnostics"]["method_attempts"][0]["method"] == "read_text"
    assert job.result_summary["parse_diagnostics"]["document_parse"]["segment_count"] == 1

    progress_resp = client.get(f"/api/v1/progress/{job.id}")
    assert progress_resp.status_code == 200
    progress = progress_resp.json()
    assert progress["result_summary"]["parse_diagnostics"]["status"] == "completed"
    assert progress["result_summary"]["parse_diagnostics"]["document_parse"]["methods"] == ["read_text"]

    status_resp = client.get(f"/api/v1/ingestion/status/{source.id}")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["parse_diagnostics"]["status"] == "completed"
    assert status["parse_diagnostics"]["method_attempts"][0]["method"] == "read_text"
    assert status["parse_diagnostics"]["document_parse"]["text_chars"] == len(text_file.read_text(encoding="utf-8"))


def test_partial_image_parse_fails_closed_before_downstream_processing(db, tmp_path, monkeypatch):
    from app.services import file_parser

    image_file = tmp_path / "partial-long-image.png"
    image_file.write_bytes(b"not-used-by-fake-parser")
    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="image",
        title="partial-long-image.png",
        storage_path=str(image_file),
        raw_text=None,
        status="pending",
        language="zh",
        word_count=0,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.commit()

    def fake_extract_text(
        _path,
        mime_type="",
        progress_cb=None,
        vision_prompt="",
        diagnostics=None,
        segment_collector=None,
    ):
        diagnostics.append(
            {
                "method": "openrouter_vision",
                "status": "partial",
                "chars": 12,
                "slices": 3,
                "completed_slices": 2,
                "failed_slices": [2],
            }
        )
        segment_collector.append(
            DocumentParseSegment(
                type="image",
                content="first\n\nthird",
                method="openrouter_vision",
                ordinal=1,
                source_name="partial-long-image.png",
            )
        )
        return "first\n\nthird"

    monkeypatch.setattr(file_parser, "extract_text", fake_extract_text)
    monkeypatch.setattr(
        IngestionWorkflow,
        "_infer_speaker_theme",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("downstream stage must not run")),
    )

    result = IngestionWorkflow(db).process_source(source.id)

    assert result["success"] is False
    assert result["error"] == "Partial document parse requires review"
    assert result["parse_diagnostics"]["status"] == "partial"
    assert result["parse_diagnostics"]["reason_code"] == "partial_parse_requires_review"
    assert result["parse_diagnostics"]["document_parse"]["status"] == "partial"
    assert result["parse_diagnostics"]["document_parse"]["slices_failed"] == [2]

    db.refresh(source)
    assert source.status == "failed"
    assert source.raw_text is None
    assert source.word_count == 0

    job = db.query(JobRun).filter(JobRun.related_object_id == source.id).one()
    assert job.status == "failed"
    assert job.result_summary["parse_diagnostics"]["reason_code"] == "partial_parse_requires_review"
