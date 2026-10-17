from fastapi import APIRouter, Depends, HTTPException, Body, Query
from typing import Any, Optional
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.workflows.committee_workflow import CommitteeWorkflow
from app.schemas.debate import (
    ADJUDICATION_METADATA_KEY,
    DebateRecord,
    DebateRound,
    FinalAdjudication,
    load_adjudication_metadata,
)
from app.schemas.job_run import JobRun
from app.utils.debate_seats import get_seat_model as _get_seat  # A6: 统一席位模型读取
from app.utils.public_llm_trace import public_llm_trace
from app.utils.source_scope import clean_source_ids

router = APIRouter()


@router.post("/run", response_model=dict)
def run_committee(
    question: str = Body(...),
    asset: str = Body(...),
    num_rounds: int = Body(2, ge=1, le=3),
    source_ids: list[str] = Body(default=[]),
    db: Session = Depends(get_db),
):
    """启动三模型委员会辩论"""
    clean_ids = clean_source_ids(source_ids)
    workflow = CommitteeWorkflow(db)
    try:
        result = workflow.run(
            question=question,
            asset=asset,
            num_rounds=num_rounds,
            source_ids=clean_ids,
        )
    except Exception as error:
        from loguru import logger

        logger.error(f"[Committee API] unhandled workflow error: {type(error).__name__}")
        raise HTTPException(
            status_code=500,
            detail={"code": "committee_execution_failed", "message": "Committee execution failed."},
        ) from None
    if not result["success"]:
        detail = {
            "code": result.get("error_code", "committee_execution_failed"),
            "message": "Committee execution failed.",
        }
        if result.get("debate_id"):
            detail["debate_id"] = result["debate_id"]
        if result.get("job_id"):
            detail["job_id"] = result["job_id"]
        raise HTTPException(status_code=500, detail=detail)
    result["llm_trace"] = public_llm_trace(result.get("llm_trace"))
    return result


@router.get("", response_model=dict)
def list_debates(
    asset: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """列出辩论记录"""
    query = db.query(DebateRecord)
    if asset:
        query = query.filter(DebateRecord.asset == asset.upper())
    if status:
        query = query.filter(DebateRecord.status == status)
    total = query.count()
    items = query.order_by(DebateRecord.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "debates": [_debate_to_dict(d) for d in items],
        "total": total,
        "has_more": (offset + limit) < total,
    }


@router.get("/{debate_id}", response_model=dict)
def get_debate(
    debate_id: str,
    db: Session = Depends(get_db),
):
    """获取单次辩论详情（含所有轮次和裁决）"""
    debate = db.get(DebateRecord, debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    rounds = (
        db.query(DebateRound)
        .filter(DebateRound.debate_id == debate_id)
        .order_by(DebateRound.round_number, DebateRound.role)
        .all()
    )
    adj = db.query(FinalAdjudication).filter(FinalAdjudication.debate_id == debate_id).first()
    job = (
        db.query(JobRun)
        .filter(JobRun.job_type == "committee_debate", JobRun.related_object_id == debate_id)
        .order_by(JobRun.created_at.desc())
        .first()
    )
    job_summary = job.result_summary if job and isinstance(job.result_summary, dict) else {}

    return {
        **_debate_to_dict(debate),
        "rounds": [_round_to_dict(r) for r in rounds],
        "adjudication": _adj_to_dict(adj) if adj else None,
        **_trust_context_to_dict(debate, adj, job_summary),
    }


def _debate_to_dict(d: DebateRecord) -> dict:
    return {
        "id": d.id,
        "question": d.question,
        "asset": d.asset,
        "committee_level": d.committee_level,
        # A6: 通过 get_seat_model 读取；优先新 JSON 列，旧数据 fallback 到 V1 列
        "model_baseline": _get_seat(d, "baseline"),
        "model_probability": _get_seat(d, "probability"),
        "model_risk": _get_seat(d, "risk"),
        "model_structure": _get_seat(d, "structure"),
        "model_by_seat": d.model_by_seat or {},  # 完整映射供前端展示
        "status": d.status,
        "final_direction": d.final_direction,
        "final_conviction": d.final_conviction,
        "final_action": d.final_action,
        "consensus_level": d.consensus_level,
        "started_at": d.started_at.isoformat() if d.started_at else None,
        "completed_at": d.completed_at.isoformat() if d.completed_at else None,
        "duration_ms": d.duration_ms,
        "error_message": d.error_message,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _round_to_dict(r: DebateRound) -> dict:
    return {
        "id": r.id,
        "round_number": r.round_number,
        "role": r.role,
        "model_used": r.model_used,
        "position": r.position,
        "key_arguments": r.key_arguments or [],
        "challenges": r.challenges or [],
        "concessions": r.concessions or [],
        "confidence": r.confidence,
        "direction": r.direction,
        "latency_ms": r.latency_ms,
        "is_mock": r.is_mock,
    }


def _adj_to_dict(a: FinalAdjudication) -> dict:
    metadata = load_adjudication_metadata(a.action_economics)
    probability_score = metadata.get(
        "probability_score",
        getattr(a, "probability_score", 0.5),
    )
    if probability_score is None:
        probability_score = 0.5
    audit_score = metadata.get("audit_score", getattr(a, "audit_score", 0.0))
    if audit_score is None:
        audit_score = 0.0
    evidence_quality_warnings = metadata.get(
        "evidence_quality_warnings",
        getattr(a, "evidence_quality_warnings", []),
    )
    if not isinstance(evidence_quality_warnings, list):
        evidence_quality_warnings = []
    action_economics = dict(a.action_economics or {})
    action_economics.pop(ADJUDICATION_METADATA_KEY, None)
    return {
        "id": a.id,
        "direction": a.direction,
        "conviction": a.conviction,
        "recommended_action": a.recommended_action,
        "consensus_level": a.consensus_level,
        "synthesis": a.synthesis,
        # V1 向后兼容
        "bull_score": a.bull_score,
        "bear_score": a.bear_score,
        # V2 语义化映射
        "baseline_score": a.bull_score,
        "probability_score": probability_score,
        "risk_score": a.bear_score,
        "structure_score": getattr(a, 'structure_score', None) or 0.5,
        "audit_score": audit_score,
        "structure_warnings": getattr(a, 'structure_warnings', None) or [],
        "risk_warnings": getattr(a, 'risk_warnings', None) or [],
        "hedging_advice": getattr(a, 'hedging_advice', None) or "",
        "action_economics": action_economics,
        "evidence_quality_warnings": evidence_quality_warnings,
        "key_agreements": a.key_agreements or [],
        "key_disagreements": a.key_disagreements or [],
        "minority_view": a.minority_view,
        "invalidation_conditions": a.invalidation_conditions or [],
        "adjudicated_by": a.adjudicated_by,
        "is_mock": a.is_mock,
    }


def _trust_context_to_dict(
    debate: DebateRecord,
    adj: FinalAdjudication | None,
    job_summary: dict[str, Any],
) -> dict:
    source_claim_ids = job_summary.get("source_claim_ids")
    if not isinstance(source_claim_ids, list):
        source_claim_ids = [
            str(claim.get("id"))
            for claim in (debate.context_claims or [])
            if isinstance(claim, dict) and claim.get("id")
        ]

    claims_used = job_summary.get("claims_used")
    if not isinstance(claims_used, int):
        claims_used = len(source_claim_ids) if source_claim_ids else len(debate.context_claims or [])

    decision_guardrail = job_summary.get("decision_guardrail")
    if not isinstance(decision_guardrail, dict):
        action_economics = getattr(adj, "action_economics", None) if adj else None
        if isinstance(action_economics, dict):
            decision_guardrail = action_economics.get("decision_guardrail")
    if not isinstance(decision_guardrail, dict):
        decision_guardrail = {}

    llm_trace = public_llm_trace(job_summary.get("llm_trace"))
    if not llm_trace:
        llm_trace = None

    scope = job_summary.get("scope")
    if not isinstance(scope, dict):
        scope = {}

    quality_filters = job_summary.get("quality_filters")
    if not isinstance(quality_filters, dict):
        quality_filters = {}
    quality_report = job_summary.get("quality_report")
    if not isinstance(quality_report, dict):
        quality_report = None
    timing_trace = job_summary.get("timing_trace")
    if not isinstance(timing_trace, dict):
        timing_trace = None
    seat_quality = job_summary.get("seat_quality")
    if not isinstance(seat_quality, dict):
        seat_quality = None

    return {
        "decision_guardrail": decision_guardrail,
        "quality_report": quality_report,
        "quality_filters": quality_filters,
        "claims_used": claims_used,
        "source_claim_ids": source_claim_ids,
        "scope": scope,
        "llm_trace": llm_trace,
        "route_identity": job_summary.get("route_identity") if isinstance(job_summary.get("route_identity"), dict) else None,
        "timing_trace": timing_trace,
        "seat_quality": seat_quality,
        "cost_usd": job_summary.get("cost_usd"),
        "llm_call_count": llm_trace.get("call_count") if isinstance(llm_trace, dict) else None,
    }
