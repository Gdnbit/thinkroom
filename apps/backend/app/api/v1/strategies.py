from fastapi import APIRouter, Depends, HTTPException, Body, Query
from typing import List, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from ulid import ULID

from app.database import safe_commit
from app.dependencies import get_db
from app.schemas.strategy_card import StrategyCard, StrategySourceLink
from app.schemas.setup_card import SetupCard
from app.services.strategy_repository import StrategyRepository
from app.workflows.strategy_distillation_workflow import StrategyDistillationWorkflow
from app.mcps.research_memory_mcp import ResearchMemoryMCP
from app.mcps.event_feed_mcp import EventFeedMCP
from app.mcps.market_data_mcp import MarketDataMCP
from app.mcps.flow_continuity_mcp import FlowContinuityMCP
from app.skills.strategy_matching_skill import StrategyMatchingSkill, StrategyMatchingInput

router = APIRouter()


@router.get("", response_model=List[dict])
def list_strategies(
    asset: Optional[str] = Query(None),
    strategy_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取策略列表"""
    repo = StrategyRepository(db)
    items, _ = repo.search(asset=asset, strategy_type=strategy_type, limit=limit)
    return [
        {
            "id": s.id,
            "strategy_name": s.strategy_name,
            "strategy_type": s.strategy_type,
            "summary": s.summary,
            "applicable_assets": s.applicable_assets,
            "applicable_horizons": s.applicable_horizons,
            "entry_conditions": s.entry_conditions,
            "confidence": s.confidence,
            "distilled_by": s.distilled_by,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in items
    ]


@router.get("/{strategy_id}", response_model=dict)
async def get_strategy(strategy_id: str, db: Session = Depends(get_db)):
    """获取单个策略详情"""
    repo = StrategyRepository(db)
    s = repo.get(strategy_id)
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {
        "id": s.id,
        "strategy_name": s.strategy_name,
        "strategy_type": s.strategy_type,
        "summary": s.summary,
        "core_logic": s.core_logic,
        "applicable_assets": s.applicable_assets,
        "applicable_horizons": s.applicable_horizons,
        "applicable_regimes": s.applicable_regimes,
        "entry_conditions": s.entry_conditions,
        "disqualifying_conditions": s.disqualifying_conditions,
        "exit_rules": s.exit_rules,
        "risk_factors": s.risk_factors,
        "confidence": s.confidence,
        "historical_win_rate": s.historical_win_rate,
        "distilled_by": s.distilled_by,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.post("/distill/{source_id}", response_model=dict)
def distill_strategy(
    source_id: str,
    db: Session = Depends(get_db),
):
    """从 RawSource 蒸馏策略规则"""
    workflow = StrategyDistillationWorkflow(db)
    result = workflow.distill_from_source(source_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/{strategy_id}/evaluate", response_model=dict)
def evaluate_strategy(
    strategy_id: str,
    asset: str = Body(...),
    db: Session = Depends(get_db),
):
    """评估策略在当前市场的匹配度，生成 SetupCard"""
    strategy = db.query(StrategyCard).filter(StrategyCard.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # 聚合信号
    memory_mcp = ResearchMemoryMCP(db)
    event_mcp = EventFeedMCP(db)
    market_mcp = MarketDataMCP(db)
    flow_mcp = FlowContinuityMCP(db)

    claims_resp = memory_mcp.get_active_claims_by_asset(asset, limit=10)
    events_resp = event_mcp.fetch_recent_events(asset=asset, limit=5)
    market_resp = market_mcp.get_market_state(asset=asset)
    flow_resp = flow_mcp.get_continuity_summary(asset=asset)

    claims = [
        {"summary": c.summary, "stance": c.stance, "confidence": c.confidence}
        for c in (claims_resp.data or [])
    ]

    # 评估
    skill = StrategyMatchingSkill()
    evaluation = skill.evaluate(StrategyMatchingInput(
        strategy_id=strategy_id,
        strategy_name=strategy.strategy_name,
        strategy_type=strategy.strategy_type,
        entry_conditions=strategy.entry_conditions,
        disqualifying_conditions=strategy.disqualifying_conditions,
        asset=asset,
        claims=claims,
        events=events_resp.data or [],
        market_state=market_resp.data,
        flow_signals=(flow_resp.data or {}).get("signals", []),
    ))

    # 持久化 SetupCard
    now = datetime.now(timezone.utc)
    setup = SetupCard(
        id=str(ULID()),
        strategy_id=strategy_id,
        asset=asset,
        direction=evaluation.direction,
        strategy_fit_score=evaluation.strategy_fit_score,
        evidence_strength_score=evaluation.evidence_strength_score,
        overall_score=evaluation.overall_score,
        matched_conditions=evaluation.matched_conditions,
        unmatched_conditions=evaluation.unmatched_conditions,
        disqualifying_flags=evaluation.disqualifying_flags,
        recommended_action=evaluation.recommended_action,
        action_notes=evaluation.action_notes,
        invalidation_conditions=evaluation.invalidation_conditions,
        supporting_claim_ids=[],
        supporting_event_ids=[e.get("id", "") for e in (events_resp.data or []) if e.get("id")],
        market_state_snapshot=market_resp.data or {},
        flow_snapshot=(flow_resp.data or {}),
        status="active",
        evaluated_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(setup)
    safe_commit(db)
    db.refresh(setup)

    return {
        "setup_id": setup.id,
        "strategy_id": strategy_id,
        "strategy_name": strategy.strategy_name,
        "asset": asset,
        "direction": evaluation.direction,
        "strategy_fit_score": evaluation.strategy_fit_score,
        "evidence_strength_score": evaluation.evidence_strength_score,
        "overall_score": evaluation.overall_score,
        "matched_conditions": evaluation.matched_conditions,
        "unmatched_conditions": evaluation.unmatched_conditions,
        "disqualifying_flags": evaluation.disqualifying_flags,
        "recommended_action": evaluation.recommended_action,
        "action_notes": evaluation.action_notes,
        "invalidation_conditions": evaluation.invalidation_conditions,
        "is_mock": evaluation.is_mock,
        "source_mode": "mock" if events_resp.is_mock else "live",
    }
