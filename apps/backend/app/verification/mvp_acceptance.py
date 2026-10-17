"""One-command MVP acceptance for the core user workflow.

The run is append-only: it creates marked fixture data and writes a JSON report
under docs/verification. It does not delete or rewrite existing records.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from ulid import ULID

from app.config import settings
from app.dependencies import SessionLocal
from app.main import app
from app.schemas.job_run import JobRun
from app.schemas.raw_source import RawSource
from app.services import semantic_search
from app.services.progress_tracker import progress_tracker


def _new_id() -> str:
    return str(ULID())


def _assert(name: str, condition: bool, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "passed" if condition else "failed", "details": details or {}}


def _raw_json_like(text: str) -> bool:
    return bool(re.match(r"^\s*[\{\[]", text or "")) or '"answer"' in (text or "")[:240]


def _mojibake_like(text: str) -> bool:
    return bool(re.search(r"[ÃÂæçèéå]", (text or "")[:500]))


def run_acceptance(write_report: bool = True) -> dict[str, Any]:
    old_mock = getattr(settings, "OPENROUTER_MOCK", False)
    old_mock_latency = getattr(settings, "OPENROUTER_MOCK_LATENCY_MS", 0)
    old_ingestion_recovery = getattr(settings, "INGESTION_RECOVERY_ENABLED", True)
    old_embed = semantic_search.embed_text
    settings.OPENROUTER_MOCK = True
    settings.OPENROUTER_MOCK_LATENCY_MS = 0
    settings.INGESTION_RECOVERY_ENABLED = False
    semantic_search.embed_text = lambda _text: None

    db = SessionLocal()
    assertions: list[dict[str, Any]] = []
    run_id = _new_id()
    marker = f"MVP_ACCEPTANCE_{run_id}"
    client = TestClient(app)

    upload_data: dict[str, Any] = {}
    process_data: dict[str, Any] = {}
    status_data: dict[str, Any] = {}
    progress_data: dict[str, Any] = {}
    async_upload_data: dict[str, Any] = {}
    async_process_data: dict[str, Any] = {}
    async_status_data: dict[str, Any] = {}
    async_progress_data: dict[str, Any] = {}
    async_db_progress_data: dict[str, Any] = {}
    cancel_job_data: dict[str, Any] = {}
    chat_data: dict[str, Any] = {}
    claim_citation_detail_data: dict[str, Any] = {}
    messages_data: list[dict[str, Any]] = []
    evidence_span_data: list[dict[str, Any]] = []

    try:
        health = client.get("/health")
        assertions.append(_assert("backend_health", health.status_code == 200, {"status_code": health.status_code}))

        fixture_text = (
            f"{marker}\n"
            "结论：当前 Research OS 的 MVP 演示应优先保证上传解析、证据引用、回答可追溯。\n"
            "理由：用户需要先确认资料确实入库，再看到回答如何引用这些资料。\n"
            "风险：长文档和多文件场景需要异步队列、阶段进度和失败重试。"
        ).encode("utf-8")
        upload_resp = client.post(
            "/api/v1/upload",
            files=[("files", (f"{marker}.txt", fixture_text, "text/plain"))],
            data={"speaker_hint": "MVP Acceptance", "theme_hint": "core workflow"},
        )
        upload_data = upload_resp.json()
        source_ids = upload_data.get("source_ids") or []
        source_id = source_ids[0] if source_ids else None
        assertions.append(
            _assert(
                "upload_txt_creates_source",
                upload_resp.status_code == 200 and bool(source_id) and upload_data.get("status") == "queued",
                {"status_code": upload_resp.status_code, "upload": upload_data},
            )
        )

        if not source_id:
            raise RuntimeError("upload did not return a source_id")

        process_resp = client.post(f"/api/v1/ingestion/process/{source_id}")
        process_data = process_resp.json()
        job_id = process_data.get("job_id")
        assertions.append(
            _assert(
                "ingestion_process_completes",
                process_resp.status_code == 200 and process_data.get("success") is True and bool(job_id),
                {"status_code": process_resp.status_code, "process": process_data},
            )
        )
        claim_ids = process_data.get("claim_ids") or []
        if claim_ids:
            from app.schemas.claim_card import ClaimEvidenceLink
            from app.schemas.evidence_span import EvidenceSpan

            evidence_links = (
                db.query(ClaimEvidenceLink)
                .filter(ClaimEvidenceLink.claim_id.in_(claim_ids))
                .all()
            )
            span_ids = [link.evidence_span_id for link in evidence_links]
            spans = db.query(EvidenceSpan).filter(EvidenceSpan.id.in_(span_ids)).all() if span_ids else []
            evidence_span_data = [
                {
                    "id": span.id,
                    "source_id": span.source_id,
                    "span_type": span.span_type,
                    "char_start": span.char_start,
                    "char_end": span.char_end,
                    "extraction_method": span.extraction_method,
                    "content_preview": (span.content or "")[:300],
                }
                for span in spans
            ]
            assertions.append(
                _assert(
                    "ingestion_persists_evidence_spans",
                    bool(evidence_links)
                    and bool(spans)
                    and any(
                        span.source_id == source_id
                        and span.content
                        and isinstance(span.char_start, int)
                        and isinstance(span.char_end, int)
                        and span.char_end > span.char_start
                        for span in spans
                    ),
                    {"claim_ids": claim_ids, "evidence_spans": evidence_span_data},
                )
            )

        status_resp = client.get(f"/api/v1/ingestion/status/{source_id}")
        status_data = status_resp.json()
        assertions.append(
            _assert(
                "ingestion_status_answer_ready_or_summarized",
                status_resp.status_code == 200
                and status_data.get("status") == "completed"
                and status_data.get("processing_stage") in {"answer_ready", "summarized"},
                {"status_code": status_resp.status_code, "status": status_data},
            )
        )

        if job_id:
            progress_resp = client.get(f"/api/v1/progress/{job_id}")
            progress_data = progress_resp.json()
            job_row = db.get(JobRun, job_id)
            persisted_snapshot = (job_row.result_summary or {}).get("progress_snapshot") if job_row else None
            assertions.append(
                _assert(
                    "progress_reports_completed_job",
                    progress_resp.status_code == 200
                    and progress_data.get("status") == "completed"
                    and int(progress_data.get("progress_pct") or 0) == 100,
                    {"status_code": progress_resp.status_code, "progress": progress_data},
                )
            )
            assertions.append(
                _assert(
                    "progress_persists_stage_snapshot",
                    job_row is not None
                    and isinstance(persisted_snapshot, dict)
                    and persisted_snapshot.get("progress_pct") == 100
                    and len(persisted_snapshot.get("stages") or []) >= 5,
                    {
                        "job_id": job_id,
                        "job_status": getattr(job_row, "status", None),
                        "persisted_progress": persisted_snapshot,
                    },
                )
            )

        async_fixture_text = (
            f"{marker}_ASYNC\n"
            "结论：前端上传后应拿到后台 job_id，并通过进度接口看到解析、抽取和入库状态。\n"
            "理由：真实用户不会等待一个阻塞请求完成，必须能确认后台仍在工作。\n"
            "风险：如果进度只存在最终结果，长文档演示会让用户误判系统卡死。"
        ).encode("utf-8")
        async_upload_resp = client.post(
            "/api/v1/upload",
            files=[("files", (f"{marker}_async.txt", async_fixture_text, "text/plain"))],
            data={"speaker_hint": "MVP Acceptance Async", "theme_hint": "async ingestion workflow"},
        )
        async_upload_data = async_upload_resp.json()
        async_source_ids = async_upload_data.get("source_ids") or []
        async_source_id = async_source_ids[0] if async_source_ids else None
        if async_source_id:
            async_process_resp = client.post(
                f"/api/v1/ingestion/process/{async_source_id}",
                params={"async_mode": True},
            )
            async_process_data = async_process_resp.json()
            async_job_id = async_process_data.get("job_id")
            assertions.append(
                _assert(
                    "async_ingestion_returns_queue_contract",
                    async_process_resp.status_code == 200
                    and async_process_data.get("queued") is True
                    and bool(async_job_id)
                    and async_process_data.get("progress_url") == f"/api/v1/progress/{async_job_id}",
                    {"status_code": async_process_resp.status_code, "process": async_process_data},
                )
            )

            for _ in range(30):
                async_status_resp = client.get(f"/api/v1/ingestion/status/{async_source_id}")
                async_status_data = async_status_resp.json()
                if async_status_data.get("status") in {"completed", "failed"}:
                    break
                time.sleep(0.1)

            if async_job_id:
                async_progress_resp = client.get(f"/api/v1/progress/{async_job_id}")
                async_progress_data = async_progress_resp.json()
                with progress_tracker._lock:
                    progress_tracker._jobs.pop(async_job_id, None)
                async_db_progress_resp = client.get(f"/api/v1/progress/{async_job_id}")
                async_db_progress_data = async_db_progress_resp.json()

            assertions.append(
                _assert(
                    "async_ingestion_status_reaches_answer_ready",
                    async_status_data.get("status") == "completed"
                    and async_status_data.get("processing_stage") in {"answer_ready", "summarized"},
                    {"source_id": async_source_id, "status": async_status_data},
                )
            )
            assertions.append(
                _assert(
                    "async_ingestion_progress_observable",
                    bool(async_job_id)
                    and async_progress_data.get("job_id") == async_job_id
                    and async_progress_data.get("workflow") == "ingestion"
                    and async_progress_data.get("status") in {"running", "completed"}
                    and isinstance(async_progress_data.get("stages"), list)
                    and len(async_progress_data.get("stages") or []) >= 5,
                    {"job_id": async_job_id, "progress": async_progress_data},
                )
            )
            assertions.append(
                _assert(
                    "async_progress_survives_memory_tracker_loss",
                    bool(async_job_id)
                    and async_db_progress_data.get("job_id") == async_job_id
                    and async_db_progress_data.get("source") == "job_run"
                    and async_db_progress_data.get("status") == "completed"
                    and int(async_db_progress_data.get("progress_pct") or 0) == 100
                    and len(async_db_progress_data.get("stages") or []) >= 5,
                    {"job_id": async_job_id, "db_progress": async_db_progress_data},
                )
            )
        else:
            assertions.append(
                _assert(
                    "async_ingestion_returns_queue_contract",
                    False,
                    {"upload_status_code": async_upload_resp.status_code, "upload": async_upload_data},
                )
            )

        cancel_source = RawSource(
            id=_new_id(),
            source_type="note",
            title=f"{marker}_cancel.txt",
            raw_text=f"{marker}\n这是一条用于验证取消任务的资料。",
            language="zh",
            status="processing",
            word_count=20,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        cancel_job = JobRun(
            id=_new_id(),
            job_type="ingestion",
            status="running",
            triggered_by="api",
            input_params={"source_id": cancel_source.id},
            result_summary={"stage": "parse", "pct": 30, "message": "cancel fixture"},
            started_at=datetime.now(timezone.utc),
            related_object_id=cancel_source.id,
            related_object_type="raw_source",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(cancel_source)
        db.add(cancel_job)
        db.commit()
        cancel_resp = client.post(
            f"/api/v1/ingestion/jobs/{cancel_job.id}/cancel",
            json={"reason": "MVP acceptance cancellation check"},
        )
        cancel_job_data = cancel_resp.json()
        db.refresh(cancel_source)
        db.refresh(cancel_job)
        assertions.append(
            _assert(
                "ingestion_job_cancel_api_converges_state",
                cancel_resp.status_code == 200
                and cancel_job.status == "cancelled"
                and cancel_source.status == "failed"
                and (cancel_job.result_summary or {}).get("stage") == "cancelled",
                {
                    "status_code": cancel_resp.status_code,
                    "cancel_response": cancel_job_data,
                    "job_status": cancel_job.status,
                    "source_status": cancel_source.status,
                    "job_summary": cancel_job.result_summary,
                    "source_error": cancel_source.processing_error,
                },
            )
        )

        session_resp = client.post(
            "/api/v1/chat/sessions",
            json={"title": f"mvp acceptance {run_id}", "mode": "chat", "asset": "BTC"},
        )
        session_resp.raise_for_status()
        session_id = session_resp.json()["id"]
        chat_resp = client.post(
            f"/api/v1/chat/sessions/{session_id}/chat",
            json={
                "message": "请只根据刚上传的资料说明：当前 MVP 演示主流程最应该保证什么？请给出结论和风险。",
                "history": [],
                "scope_mode": "selected_sources",
                "source_ids": [source_id],
            },
        )
        chat_data = chat_resp.json()
        content = chat_data.get("content") or ""
        assertions.append(
            _assert(
                "chat_selected_scope_answer_is_user_facing",
                chat_resp.status_code == 200
                and len(content.strip()) >= 20
                and not _raw_json_like(content)
                and not _mojibake_like(content),
                {"status_code": chat_resp.status_code, "content_preview": content[:500]},
            )
        )
        assertions.append(
            _assert(
                "chat_selected_scope_metadata",
                (chat_data.get("scope") or {}).get("mode") == "selected_sources"
                and (chat_data.get("scope") or {}).get("source_ids") == [source_id],
                {"scope": chat_data.get("scope")},
            )
        )
        assertions.append(
            _assert(
                "chat_quality_filters_visible",
                bool((chat_data.get("quality_filters") or {}).get("exclude_expired"))
                and bool((chat_data.get("quality_filters") or {}).get("exclude_contradicted")),
                {"quality_filters": chat_data.get("quality_filters")},
            )
        )
        assertions.append(
            _assert(
                "chat_has_citation_or_source_evidence",
                bool(chat_data.get("citations")),
                {"citations": chat_data.get("citations")},
            )
        )
        assertions.append(
            _assert(
                "chat_uses_persisted_evidence_when_claim_available",
                int(chat_data.get("claims_used") or 0) >= 1
                and any(
                    c.get("evidence_span_id")
                    and c.get("span_type") in {"quote", "source_window"}
                    and c.get("source_id") == source_id
                    for c in (chat_data.get("citations") or [])
                    if isinstance(c, dict)
                ),
                {
                    "claims_used": chat_data.get("claims_used"),
                    "citations": chat_data.get("citations"),
                },
            )
        )
        first_citation = (chat_data.get("citations") or [{}])[0]
        first_claim_id = first_citation.get("claim_id")
        first_evidence_span_id = first_citation.get("evidence_span_id")
        if isinstance(first_claim_id, str) and first_claim_id and not first_claim_id.startswith("source:"):
            claim_citation_resp = client.get(f"/api/v1/claims/{first_claim_id}/citation")
            claim_citation_detail_data = claim_citation_resp.json()
            detail_spans = claim_citation_detail_data.get("evidence_spans") or []
            detail_sources = claim_citation_detail_data.get("sources") or []
            assertions.append(
                _assert(
                    "claim_citation_detail_resolves_persisted_span",
                    claim_citation_resp.status_code == 200
                    and claim_citation_detail_data.get("claim", {}).get("id") == first_claim_id
                    and any(span.get("id") == first_evidence_span_id for span in detail_spans)
                    and any(source.get("id") == source_id for source in detail_sources),
                    {
                        "status_code": claim_citation_resp.status_code,
                        "claim_id": first_claim_id,
                        "evidence_span_id": first_evidence_span_id,
                        "source_id": source_id,
                        "detail": claim_citation_detail_data,
                    },
                )
            )
        assertions.append(
            _assert(
                "chat_source_evidence_has_span_location",
                first_citation.get("source_id") == source_id
                and bool(first_citation.get("short_quote"))
                and first_citation.get("span_type") in {"raw_text", "article_summary", "quote", "source_window"}
                and (
                    first_citation.get("span_type") == "article_summary"
                    or (
                        isinstance(first_citation.get("char_start"), int)
                        and isinstance(first_citation.get("char_end"), int)
                        and first_citation.get("char_end") > first_citation.get("char_start")
                    )
                ),
                {"citation": first_citation},
            )
        )

        messages_resp = client.get(f"/api/v1/chat/sessions/{session_id}/messages")
        messages_data = messages_resp.json()
        assertions.append(
            _assert(
                "chat_history_persisted",
                messages_resp.status_code == 200
                and len(messages_data) >= 2
                and messages_data[-1].get("role") == "assistant",
                {"status_code": messages_resp.status_code, "message_count": len(messages_data)},
            )
        )

        status = "passed" if all(a["status"] == "passed" for a in assertions) else "failed"
        report = {
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "marker": marker,
            "assertions": assertions,
            "artifacts": {
                "source_id": source_id,
                "job_id": job_id,
                "async_source_id": async_source_id if "async_source_id" in locals() else None,
                "async_job_id": async_process_data.get("job_id"),
                "cancel_job_id": cancel_job_data.get("job_id"),
                "session_id": session_id,
                "chat_message_id": chat_data.get("id"),
            },
            "snapshots": {
                "upload": upload_data,
                "process": process_data,
                "source_status": status_data,
                "progress": progress_data,
                "async_upload": async_upload_data,
                "async_process": async_process_data,
                "async_source_status": async_status_data,
                "async_progress": async_progress_data,
                "async_db_progress": async_db_progress_data,
                "cancel_job": cancel_job_data,
                "evidence_spans": evidence_span_data,
                "claim_citation_detail": claim_citation_detail_data,
                "chat": {
                    "id": chat_data.get("id"),
                    "claims_used": chat_data.get("claims_used"),
                    "model_used": chat_data.get("model_used"),
                    "scope": chat_data.get("scope"),
                    "quality_filters": chat_data.get("quality_filters"),
                    "citations": chat_data.get("citations"),
                    "content_preview": content[:1000],
                },
                "messages_tail": messages_data[-2:],
            },
            "notes": [
                "This acceptance run uses OPENROUTER_MOCK=True to keep it deterministic and low-cost.",
                "It validates workflow plumbing, persistence, scope, progress, and user-facing answer shape.",
                "Created records are preserved for future audit and are not deleted.",
            ],
        }

        if write_report:
            root = Path(__file__).resolve().parents[4]
            report_dir = root / "docs" / "verification"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"mvp_acceptance_{run_id}.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            report["report_path"] = str(report_path)
        return report
    finally:
        db.close()
        settings.OPENROUTER_MOCK = old_mock
        settings.OPENROUTER_MOCK_LATENCY_MS = old_mock_latency
        settings.INGESTION_RECOVERY_ENABLED = old_ingestion_recovery
        semantic_search.embed_text = old_embed


if __name__ == "__main__":
    result = run_acceptance(write_report=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["status"] == "passed" else 1)
