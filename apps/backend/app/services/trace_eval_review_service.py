from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session
from ulid import ULID

from app.schemas.decision_trace_eval_review import DecisionTraceEvalReview


ALLOWED_TRACE_EVAL_REVIEW_STATUSES = {
    "pending_review",
    "approved_for_manifest",
    "needs_follow_up",
    "rejected",
    "ignored",
}

TRACE_EVAL_REVIEW_NEXT_STEPS = {
    "pending_review": "review_before_promoting_to_main_manifest",
    "approved_for_manifest": "export_replay_fixture_and_promote_to_main_manifest",
    "needs_follow_up": "inspect_trace_before_promotion",
    "rejected": "no_action_required_trace_rejected",
    "ignored": "no_action_required_trace_ignored",
}


def recommended_next_step_for_review_status(review_status: str) -> str:
    return TRACE_EVAL_REVIEW_NEXT_STEPS.get(review_status, "review_before_promoting_to_main_manifest")


def serialize_trace_eval_review(review: DecisionTraceEvalReview) -> dict[str, Any]:
    return {
        "id": review.id,
        "candidate_id": review.candidate_id,
        "review_status": review.review_status,
        "reviewer": review.reviewer,
        "decision_note": review.decision_note,
        "promoted_case_id": review.promoted_case_id,
        "review_metadata": review.review_metadata or {},
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
    }


def _safe_candidate_filename(candidate_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in candidate_id)


def _default_promotion_draft_path(candidate_id: str) -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    safe_id = _safe_candidate_filename(candidate_id)
    return repo_root / "docs" / "verification" / f"main_workflow_eval_manifest.{safe_id}.implemented_draft.json"


def _replay_command_for_fixture(fixture_path: str | Path) -> str:
    repo_root = Path(__file__).resolve().parents[4]
    resolved_fixture_path = Path(fixture_path).resolve()
    try:
        relative = resolved_fixture_path.relative_to(repo_root)
        fixture_arg = str(Path("..") / ".." / relative).replace("/", "\\")
    except ValueError:
        fixture_arg = str(resolved_fixture_path)
    return f".\\.venv\\Scripts\\python.exe -m app.verification.trace_eval_replay --fixture {fixture_arg}"


def get_trace_eval_review_map(
    db: Session,
    candidate_ids: list[str],
) -> dict[str, DecisionTraceEvalReview]:
    if not candidate_ids:
        return {}
    rows = (
        db.query(DecisionTraceEvalReview)
        .filter(DecisionTraceEvalReview.candidate_id.in_(candidate_ids))
        .all()
    )
    return {row.candidate_id: row for row in rows}


def upsert_trace_eval_review(
    db: Session,
    *,
    candidate_id: str,
    review_status: str,
    reviewer: str | None = None,
    decision_note: str | None = None,
    promoted_case_id: str | None = None,
    review_metadata: dict[str, Any] | None = None,
) -> DecisionTraceEvalReview:
    if review_status not in ALLOWED_TRACE_EVAL_REVIEW_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_TRACE_EVAL_REVIEW_STATUSES))
        raise ValueError(f"Unsupported trace eval review status: {review_status}. Allowed: {allowed}")

    review = (
        db.query(DecisionTraceEvalReview)
        .filter(DecisionTraceEvalReview.candidate_id == candidate_id)
        .one_or_none()
    )
    if review is None:
        review = DecisionTraceEvalReview(
            id=str(ULID()),
            candidate_id=candidate_id,
        )
        db.add(review)

    review.review_status = review_status
    review.reviewer = reviewer
    review.decision_note = decision_note
    review.promoted_case_id = promoted_case_id
    review.review_metadata = review_metadata or {}
    db.flush()
    return review


def prepare_trace_eval_promotion_draft(
    db: Session,
    *,
    candidate_id: str,
    reviewer_note: str,
    review_manifest_output_path: str | Path | None = None,
    replay_fixture_output_dir: str | Path | None = None,
    draft_output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Export a replay fixture and implemented manifest draft for an approved trace candidate."""

    from app.verification.main_workflow_eval_manifest import default_manifest_path
    from app.verification.trace_eval_corpus import (
        default_trace_eval_review_manifest_path,
        write_trace_eval_review_manifest,
    )
    from app.verification.trace_eval_promotion import promote_trace_eval_candidate_to_manifest_draft
    from app.verification.trace_eval_replay import (
        validate_trace_eval_replay_fixture,
        write_trace_eval_replay_fixture,
    )

    review = (
        db.query(DecisionTraceEvalReview)
        .filter(DecisionTraceEvalReview.candidate_id == candidate_id)
        .one_or_none()
    )
    if review is None or review.review_status != "approved_for_manifest":
        raise ValueError("Trace eval candidate must be approved_for_manifest before draft promotion")
    if not reviewer_note.strip():
        raise ValueError("reviewer_note is required")

    resolved_review_manifest_path = (
        Path(review_manifest_output_path)
        if review_manifest_output_path
        else default_trace_eval_review_manifest_path()
    )
    review_manifest = write_trace_eval_review_manifest(
        db,
        output_path=resolved_review_manifest_path,
        limit=1000,
    )
    if not any(
        isinstance(item, dict)
        and isinstance(item.get("candidate"), dict)
        and item["candidate"].get("id") == candidate_id
        for item in review_manifest.get("candidates", [])
    ):
        raise ValueError(f"Trace eval candidate not found in review manifest: {candidate_id}")

    replay_fixture_path = write_trace_eval_replay_fixture(
        db,
        candidate_id=candidate_id,
        output_dir=replay_fixture_output_dir,
    )
    replay_validation = validate_trace_eval_replay_fixture(replay_fixture_path)
    if replay_validation["status"] != "passed":
        raise ValueError(f"replay fixture validation failed: {replay_validation['errors']}")

    resolved_draft_output_path = Path(draft_output_path) if draft_output_path else _default_promotion_draft_path(candidate_id)
    report = promote_trace_eval_candidate_to_manifest_draft(
        review_manifest_path=resolved_review_manifest_path,
        main_manifest_path=default_manifest_path(),
        candidate_id=candidate_id,
        command=_replay_command_for_fixture(replay_fixture_path),
        reviewer_note=reviewer_note,
        output_path=resolved_draft_output_path,
        coverage_status="implemented",
        replay_fixture_path=replay_fixture_path,
    )
    report["replay_fixture_path"] = str(replay_fixture_path)
    report["replay_fixture_validation"] = replay_validation

    metadata = dict(review.review_metadata or {})
    metadata["promotion_draft"] = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "output_path": report["output_path"],
        "replay_fixture_path": report["replay_fixture_path"],
        "review_manifest_path": str(resolved_review_manifest_path),
        "manifest_validation_status": report["manifest_validation"]["status"],
    }
    review.promoted_case_id = candidate_id
    review.review_metadata = metadata
    db.flush()
    report["review_decision"] = serialize_trace_eval_review(review)
    return report
