"""Text statistics helpers for ingestion and source status APIs."""
from __future__ import annotations

import re
from typing import Any


_WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)?|[\u4e00-\u9fff]")


def estimate_text_stats(text: str | None, fallback_count: int | None = None) -> dict[str, Any]:
    """Return explicit text stats without overloading legacy word_count.

    `fallback_count` exists for old rows where raw_text may be missing but the legacy
    count was already populated. In that case it is safest to expose it as char_count
    compatibility data rather than pretend it is an exact word count.
    """
    value = text or ""
    char_count = len(value) if value else int(fallback_count or 0)
    word_count_est = len(_WORD_RE.findall(value)) if value else None
    token_count_est = max(1, int(char_count / 3.2)) if char_count else 0
    return {
        "char_count": char_count,
        "word_count_est": word_count_est,
        "token_count_est": token_count_est,
        "long_document_flag": char_count >= 120_000,
    }


def infer_claim_extraction_status(source_status: str, claim_count: int, processing_error: str | None = None) -> str:
    if source_status == "failed":
        return "failed"
    if source_status in ("pending", "processing"):
        return "not_started" if claim_count == 0 else "running"
    if processing_error:
        return "failed"
    if claim_count > 0:
        return "completed"
    return "zero_claims"


def build_processing_stage(source_status: str, claim_status: str, has_raw_text: bool, has_summary: bool) -> str:
    if source_status == "failed":
        return "failed"
    if source_status in ("pending", "processing"):
        return source_status
    if not has_raw_text:
        return "uploaded"
    if not has_summary:
        return "parsed"
    if claim_status in ("zero_claims", "deferred"):
        return "summarized"
    if claim_status == "completed":
        return "answer_ready"
    return "summarized"


def build_source_status_payload(source, claim_count: int = 0) -> dict[str, Any]:
    stats = estimate_text_stats(getattr(source, "raw_text", None), getattr(source, "word_count", 0))
    claim_status = infer_claim_extraction_status(
        getattr(source, "status", ""),
        claim_count,
        getattr(source, "processing_error", None),
    )
    if (
        stats["long_document_flag"]
        and getattr(source, "status", "") == "completed"
        and claim_count == 0
    ):
        claim_status = "deferred"
    stage = build_processing_stage(
        getattr(source, "status", ""),
        claim_status,
        bool(getattr(source, "raw_text", None)),
        bool(getattr(source, "article_summary", None)),
    )
    return {
        **stats,
        "processing_stage": stage,
        "claim_extraction_status": claim_status,
        "claims_count": claim_count,
    }
