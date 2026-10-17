import re
import time
from fastapi import APIRouter, Depends, HTTPException, Body, Query, Request, Response
from typing import Any, List, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from ulid import ULID

from app.config import settings
from app.database import safe_commit
from app.dependencies import get_db
from app.models.briefing import BriefingRequest, BriefingCardResponse
from app.observability.rate_limit import limiter
from app.services.briefing_repository import BriefingRepository
from app.services.decision_snapshot_service import create_briefing_decision_snapshot
from app.services.briefing_route_identity import briefing_route_identity
from app.mcps.research_memory_mcp import ResearchMemoryMCP
from app.mcps.event_feed_mcp import EventFeedMCP
from app.mcps.market_data_mcp import MarketDataMCP
from app.mcps.flow_continuity_mcp import FlowContinuityMCP
from app.skills.fusion_skill import FusionSkill, FusionInput
from app.skills.briefing_skill import BriefingSkill, BriefingInput
from app.schemas.briefing_card import BriefingCard
from app.schemas.job_run import JobRun
from app.utils.decision_guardrail import build_structured_decision_guardrail
from app.utils.public_llm_trace import public_llm_trace
from app.utils.source_scope import clean_source_ids
from app.utils.structured_quality_report import (
    build_field_evidence_contract,
    build_structured_quality_report,
    field_evidence_warnings,
)
from app.utils.timing_trace import build_timing_trace

router = APIRouter()

_BRIEFING_FIELD_NAMES = [
    "direction",
    "recommended_action",
    "executive_summary",
    "supporting_points",
    "contrary_points",
    "key_risks",
]
_QUESTION_REFERENCE_DATE_RE = re.compile(
    r"20\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|"
    r"20\d{2}年\d{1,2}月(?:\d{1,2}日)?"
)


def _apply_selected_source_evidence_skeleton(
    briefing_output: Any,
    *,
    question: str,
    source_ids: list[str],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    """Own selected-source facts deterministically; leave the model only analysis fields."""
    if not source_ids or not claims:
        return {"applied": False, "version": None, "claim_count": 0, "conflict": False}
    summaries: list[str] = []
    stances: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        summary = str(
            claim.get("summary")
            or claim.get("full_quote")
            or claim.get("contextual_text")
            or ""
        ).strip()
        if summary and summary not in summaries:
            summaries.append(summary[:180])
        stance = str(claim.get("stance") or "").strip().lower()
        if stance in {"bullish", "bearish"}:
            stances.add(stance)
    if not summaries:
        return {"applied": False, "version": None, "claim_count": 0, "conflict": False}

    match = _QUESTION_REFERENCE_DATE_RE.search(question or "")
    reference_time = match.group(0) if match else datetime.now(
        timezone(timedelta(hours=8))
    ).strftime("%Y-%m-%d")
    conflict = {"bullish", "bearish"}.issubset(stances)
    boundary = (
        "选定资料的方向相互冲突，系统保留分歧，不选择其中一方。"
        if conflict
        else "当前结论仅限于上述选定资料，不代表已获得范围之外的独立验证。"
    )
    briefing_output.title = "选定资料证据简报"
    briefing_output.topic = "证据分析"
    briefing_output.executive_summary = (
        f"参考时间为{reference_time}。选定资料明确记录："
        + "；".join(summaries[:3]).rstrip("。； ")
        + f"。{boundary}"
    )
    briefing_output.supporting_points = list(summaries[:6])
    briefing_output.contrary_points = [
        (
            "选定资料中的观点方向相互冲突，当前不能形成单一方向结论。"
            if conflict
            else "当前选定范围不足以证明该观点已获得独立交叉验证。"
        ),
        "当前结论未使用选定范围之外的信息进行交叉验证。",
    ]
    briefing_output.strategy_fit_summary = (
        "前提：仅使用选定资料 -> 原始证据：结构化 Claim -> "
        + (
            "可支持判断：资料存在方向分歧并应保留分歧 -> 不可支持判断：任一方向已经胜出"
            if conflict
            else "可支持判断：保留资料明确陈述 -> 不可支持判断：外部事实已经确认"
        )
        + " -> 待验证条件：补充独立来源后复核"
    )
    briefing_output.invalidation_conditions = [
        "出现与选定资料相反或更高可信度的新证据时重新评估",
        "选定资料被撤回、修订或超过适用时间范围时重新评估",
    ]
    briefing_output.recommended_action = "watch"
    if conflict:
        briefing_output.direction = "neutral"
    return {
        "applied": True,
        "version": "selected_claim_evidence_skeleton_v1",
        "claim_count": len(summaries),
        "conflict": conflict,
    }


def _build_briefing_field_evidence(
    briefing_output: Any,
    *,
    quality_filters: dict[str, Any],
    source_ids: list[str],
    claims: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any], dict[str, dict[str, Any]]]:
    field_values = {
        "direction": briefing_output.direction,
        "recommended_action": briefing_output.recommended_action,
        "executive_summary": briefing_output.executive_summary,
        "supporting_points": briefing_output.supporting_points,
        "contrary_points": briefing_output.contrary_points,
        "key_risks": briefing_output.key_risks,
    }
    field_evidence = build_field_evidence_contract(
        field_names=_BRIEFING_FIELD_NAMES,
        decision_ready=True,
        quality_filters=quality_filters,
        source_ids=source_ids,
        claim_ids=[
            claim.get("id", "")
            for claim in claims
            if isinstance(claim, dict) and claim.get("id")
        ],
        event_ids=[
            event.get("id", "")
            for event in events
            if isinstance(event, dict) and event.get("id")
        ],
        field_values=field_values,
        claim_catalog=claims,
        inference_fields=["supporting_points"],
        basis_fields=["direction", "executive_summary"],
        scope_fields=["contrary_points"],
        model_fields=["key_risks"],
        policy_fields=["recommended_action"],
    )
    return list(_BRIEFING_FIELD_NAMES), field_values, field_evidence


def _prune_unverified_supporting_points(
    briefing_output: Any,
    field_evidence: dict[str, dict[str, Any]],
) -> int:
    receipt = field_evidence.get("supporting_points") or {}
    if receipt.get("status") != "unverified":
        return 0
    raw_indexes = receipt.get("unverified_item_indexes") or []
    indexes = {
        int(index)
        for index in raw_indexes
        if isinstance(index, int) or (isinstance(index, str) and index.isdigit())
    }
    if not indexes:
        return 0
    original = list(briefing_output.supporting_points or [])
    briefing_output.supporting_points = [
        item for index, item in enumerate(original) if index not in indexes
    ]
    return len(original) - len(briefing_output.supporting_points)


def _selected_scope_insufficiency_detail(reason: str | None) -> str:
    if reason == "selected_sources_no_usable_evidence":
        return "当前选定资料尚未产生可用文本，可能仍在解析、切片、向量化，或处理失败；"
    if reason == "selected_sources_claim_validity_window_mismatch":
        return "当前选定资料已形成结构化观点，但结构化观点尚未生效或已经超过有效期；"
    if reason == "selected_sources_claim_temporal_horizon_mismatch":
        return "当前选定资料已形成结构化观点，但结构化观点的时间跨度与本次问题要求不一致；"
    if reason == "selected_sources_actionable_question_requires_directional_evidence":
        return "当前选定资料提到了相关对象，但缺少可支撑操作建议的方向性证据、时间窗口或交易依据；"
    if reason == "selected_sources_temporal_question_requires_time_window_evidence":
        return "当前选定资料提到了相关对象和方向，但缺少可支撑该时间窗口的日期、近期性或预测周期证据；"
    if reason == "selected_sources_temporal_evidence_is_stale_for_current_question":
        return "当前选定资料提到了相关对象和方向，但时间锚点过旧、时效性不足，不能支撑当前或下周的操作判断；"
    if reason == "selected_sources_temporal_horizon_mismatch":
        return "当前选定资料提到了相关对象和方向，但资料中的预测周期与本次问题要求的时间跨度不一致；"
    return "当前选定资料没有覆盖该问题的关键对象、时间窗口或交易依据；"


def _briefing_created_at(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat() + "Z" if not value.tzinfo else value.isoformat()


@router.get("", response_model=List[dict])
def list_briefings(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """获取最近的简报列表"""
    repo = BriefingRepository(db)
    items = repo.get_recent(limit=limit)
    return [
        {
            "id": b.id,
            "title": b.title,
            "topic": b.topic,
            "direction": b.direction,
            "setup_quality": b.setup_quality,
            "recommended_action": b.recommended_action,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in items
    ]


@router.post("", response_model=dict)
@limiter.limit(settings.RATE_LIMIT_BRIEFING)
def generate_briefing(
    request: Request,
    response: Response,
    payload: BriefingRequest = Body(...),
    db: Session = Depends(get_db),
):
    """生成研究简报 — 完整链路：MCP 聚合 → Fusion → Briefing（限流：RATE_LIMIT_BRIEFING）"""
    source_ids = clean_source_ids(payload.source_ids)
    # 资产识别（A1：统一调用 asset_alias.detect_asset）
    from app.utils.asset_alias import detect_asset, canonicalize
    if payload.asset_scope:
        asset = canonicalize(payload.asset_scope[0]) or "GENERAL"
    else:
        asset = detect_asset(payload.question, default="GENERAL")
    now = datetime.now(timezone.utc)
    start_ts = time.time()
    started_monotonic = time.perf_counter()
    phase_started = started_monotonic
    phase_timings: dict[str, int] = {}
    route_identity = briefing_route_identity()

    # G3: 创建 job_run 记录
    job = JobRun(
        id=str(ULID()),
        job_type="briefing_gen",
        status="running",
        triggered_by="api",
        input_params={
            "question": payload.question,
            "asset": asset,
            "asset_scope": payload.asset_scope,
            "source_ids": source_ids,
            "committee_level": payload.committee_level,
        },
        result_summary={},
        source_ids=source_ids,
        object_scope={
            "asset": asset,
            "question": payload.question,
            "source_ids": source_ids,
        },
        started_at=now,
        retry_count=0,
        related_object_type="briefing",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    # Persist the audit envelope before doing provider or repository work. This
    # keeps a failed run observable even when the main unit of work rolls back.
    safe_commit(db)

    # A3: 启用 cost_scope 累计本次 briefing 生成的所有 LLM 调用成本
    from app.utils.cost_scope import CostScope
    from app.utils.cost_scope import _active_scopes as _cs_ctx
    _cs_scope = CostScope(tag="generate_briefing")
    _cs_token = _cs_ctx.set(_cs_ctx.get() + [_cs_scope])
    phase_timings["setup_ms"] = int((time.perf_counter() - phase_started) * 1000)

    try:
        # 1. 聚合 MCP 信号
        memory_mcp = ResearchMemoryMCP(db)
        event_mcp = EventFeedMCP(db)
        market_mcp = MarketDataMCP(db)
        flow_mcp = FlowContinuityMCP(db)

        response_scope = {
            "mode": "selected_sources" if source_ids else "global",
            "source_ids": source_ids,
            "asset": asset,
        }
        selected_scope_insufficient_reason = None
        selected_scope_ctx: Any | None = None
        if source_ids:
            from app.services.chat_harness import build_chat_context
            from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

            phase_started = time.perf_counter()
            selected_scope_ctx = build_chat_context(
                db=db,
                query=payload.question,
                asset_filter=None if asset == "GENERAL" else asset,
                limit=12,
                include_market=False,
                source_ids=source_ids,
                exclude_expired=True,
                exclude_contradicted=True,
            )
            selected_scope_insufficient_reason = selected_scope_insufficiency_reason(
                payload.question,
                selected_scope_ctx,
            )
            phase_timings["selected_scope_check_ms"] = int(
                (time.perf_counter() - phase_started) * 1000
            )
        else:
            phase_timings["selected_scope_check_ms"] = 0

        if selected_scope_insufficient_reason:
            phase_timings["retrieval_ms"] = 0
            phase_started = time.perf_counter()
            insufficiency_detail = _selected_scope_insufficiency_detail(selected_scope_insufficient_reason)
            reference_time = now.astimezone(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日 %H:%M:%S CST")
            quality_filters = {
                "evidence_sufficiency": "insufficient",
                "insufficient_evidence_reason": selected_scope_insufficient_reason,
            }
            executive_summary = (
                f"截至 {reference_time}，选定资料不足以支持对“{payload.question.strip()[:80]}”生成方向性交易简报。"
                f"{insufficiency_detail}"
                "因此本次仅保留为观察，不生成上涨、下跌、买入、卖出或加仓等操作结论。"
            )
            briefing_guardrail = build_structured_decision_guardrail(
                mode="briefing",
                recommended_action="watch",
                evidence_count=0,
                source_mode="insufficient_evidence",
                warnings=[insufficiency_detail],
                is_mock=False,
                confidence=0.0,
            )
            briefing = BriefingCard(
                id=str(ULID()),
                title="选定资料不足",
                topic=payload.topic or "证据不足",
                question=payload.question,
                direction="neutral",
                setup_quality="insufficient",
                time_horizon=payload.time_horizon,
                asset_scope=payload.asset_scope or [asset],
                executive_summary=executive_summary,
                supporting_points=[],
                contrary_points=[],
                key_risks=[insufficiency_detail],
                strategy_fit_summary=None,
                recommended_action=briefing_guardrail["effective_action"],
                invalidation_conditions=[
                    "补充覆盖目标对象、时间窗口、方向性依据和操作条件的资料后重新生成。"
                ],
                source_claim_ids=[],
                source_event_ids=[],
                model_used="not_called_insufficient_evidence",
                committee_level=payload.committee_level,
                status="active",
                generated_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(briefing)
            db.flush()
            create_briefing_decision_snapshot(
                db,
                briefing=briefing,
                question=payload.question,
                asset=asset,
                claims=[],
                events=[],
                market_state={},
                flow_signals=[],
                data_coverage={
                    "scope_mode": response_scope["mode"],
                    "evidence_sufficiency": quality_filters["evidence_sufficiency"],
                },
                decision_guardrail=briefing_guardrail,
                source_mode="insufficient_evidence",
                source_ids=source_ids,
            )

            quality_elapsed_ms = int((time.perf_counter() - started_monotonic) * 1000)
            llm_trace = public_llm_trace({
                **_cs_scope.to_dict(),
                "model": "not_called_insufficient_evidence",
                "error_type": "insufficient_evidence",
            })
            quality_report = build_structured_quality_report(
                decision_ready=False,
                quality_filters=quality_filters,
                claims_used=0,
                events_used=0,
                has_market=False,
                flow_signals_used=0,
                warnings=[{"code": "insufficient_evidence", "message": insufficiency_detail}],
                duration_ms=quality_elapsed_ms,
                reference_time=reference_time,
                source_mode="insufficient_evidence",
                setup_quality="insufficient",
                field_names=_BRIEFING_FIELD_NAMES,
                source_ids=source_ids,
            )
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.duration_ms = quality_elapsed_ms
            job.related_object_id = briefing.id
            job.result_summary = {
                "briefing_id": briefing.id,
                "direction": "neutral",
                "setup_quality": "insufficient",
                "claims_used": 0,
                "events_used": 0,
                "is_mock": False,
                "source_mode": "insufficient_evidence",
                "duration_ms": quality_elapsed_ms,
                "cost_usd": 0.0,
                "llm_call_count": 0,
                "model_used": "not_called_insufficient_evidence",
                "decision_guardrail": briefing_guardrail,
                "llm_trace": llm_trace,
                "quality_report": quality_report,
                "scope": response_scope,
                "quality_filters": quality_filters,
                "route_identity": route_identity,
            }
            job.updated_at = datetime.now(timezone.utc)

            from app.services.decision_trace_service import record_decision_trace_bundle, record_trace_event

            source_scope_meta = (
                getattr(selected_scope_ctx, "source_scope", {}) if selected_scope_ctx is not None else {}
            )
            record_trace_event(
                db,
                related_object_type="briefing",
                related_object_id=briefing.id,
                event_type="retrieval",
                step_name="briefing_context",
                status="warning",
                job_id=job.id,
                source_ids=source_ids,
                payload={
                    "scope_mode": response_scope["mode"],
                    "scope_resolved_from": "request_source_ids",
                    "search_tier": "selected_source_sufficiency",
                    "claims_count": 0,
                    "events_count": 0,
                    "has_market": False,
                    "flow_signals_count": 0,
                    "context_items": 0,
                    "source_scope": source_scope_meta or response_scope,
                    "quality_filters": quality_filters,
                },
            )
            record_decision_trace_bundle(
                db,
                related_object_type="briefing",
                related_object_id=briefing.id,
                job_id=job.id,
                source_ids=source_ids,
                scope=response_scope,
                quality_report=quality_report,
                decision_guardrail=briefing_guardrail,
                llm_trace=llm_trace,
            )
            phase_timings["persistence_setup_ms"] = int(
                (time.perf_counter() - phase_started) * 1000
            )
            duration_ms = int((time.perf_counter() - started_monotonic) * 1000)
            timing_trace = build_timing_trace(
                total_ms=duration_ms,
                llm_calls=llm_trace.get("calls") if isinstance(llm_trace.get("calls"), list) else [],
                phase_timings=phase_timings,
            )
            quality_report["duration_ms"] = duration_ms
            job.duration_ms = duration_ms
            job.result_summary = {
                **(job.result_summary or {}),
                "duration_ms": duration_ms,
                "timing_trace": timing_trace,
                "quality_report": quality_report,
            }
            safe_commit(db)
            db.refresh(briefing)
            _cs_ctx.reset(_cs_token)
            return {
                "id": briefing.id,
                "title": briefing.title,
                "topic": briefing.topic,
                "direction": briefing.direction,
                "setup_quality": briefing.setup_quality,
                "executive_summary": briefing.executive_summary,
                "supporting_points": briefing.supporting_points,
                "contrary_points": briefing.contrary_points,
                "key_risks": briefing.key_risks,
                "strategy_fit_summary": briefing.strategy_fit_summary,
                "recommended_action": briefing.recommended_action,
                "decision_guardrail": briefing_guardrail,
                "quality_report": quality_report,
                "invalidation_conditions": briefing.invalidation_conditions,
                "is_mock": False,
                "source_mode": "insufficient_evidence",
                "scope": response_scope,
                "quality_filters": quality_filters,
                "claims_used": 0,
                "source_claim_ids": [],
                "data_freshness": "insufficient_evidence",
                "job_id": job.id,
                "duration_ms": duration_ms,
                "timing_trace": timing_trace,
                "cost_usd": 0.0,
                "llm_call_count": 0,
                "llm_trace": llm_trace,
                "route_identity": route_identity,
                "created_at": _briefing_created_at(briefing.created_at),
                "model_used": "not_called_insufficient_evidence",
                "data_sources": {
                    "claims": {"is_mock": False, "count": 0, "items": []},
                    "events": {"is_mock": False, "count": 0, "items": []},
                    "market": {"is_mock": False, "asset": asset, "snapshot": {}},
                    "flow": {"is_mock": False, "signals_count": 0, "snapshot": {}},
                },
            }

        # A3 (D-B1)：统一使用 query_for_briefing 三路混合召回，替换劣化的 get_active_claims_by_asset
        # 收益：语义相似 + 资产精确匹配 + 高信任保底；claims 字段更完整（含 id/trust_weight/retrieval_paths）
        phase_started = time.perf_counter()
        claims_resp = memory_mcp.query_for_briefing(
            query=payload.question, asset=asset, limit=12, source_ids=source_ids
        )
        # 数据覆盖摘要：告知 LLM 本地有哪些私域数据，避免幻觉补充
        data_coverage = memory_mcp.get_data_coverage_summary(asset=asset, source_ids=source_ids)
        if source_ids:
            # These MCPs have no source_id linkage. Mixing them into an explicit
            # selected-source request would silently widen both evidence and cost.
            from app.mcps.base import MCPResponse

            events_resp = MCPResponse.ok([], source_package="selected_source_scope")
            market_resp = MCPResponse.ok({}, source_package="selected_source_scope")
            flow_resp = MCPResponse.ok({"signals": []}, source_package="selected_source_scope")
        else:
            events_resp = event_mcp.fetch_recent_events(asset=asset, limit=5)
            market_resp = market_mcp.get_market_state(asset=asset)
            flow_resp = flow_mcp.get_continuity_summary(asset=asset)

        # query_for_briefing 已返回 dict 列表，直接使用
        claims = claims_resp.data or []
        events = events_resp.data or []
        market_state = market_resp.data
        flow_signals = (flow_resp.data or {}).get("signals", [])
        phase_timings["retrieval_ms"] = int((time.perf_counter() - phase_started) * 1000)

        # G2: 判断整体 source_mode。私域 claims 是有效证据，不能因外部行情/事件源为空而误判为全 mock。
        has_private_evidence = bool(claims)
        all_mock = (not has_private_evidence) and events_resp.is_mock and market_resp.is_mock and flow_resp.is_mock
        any_mock = events_resp.is_mock or market_resp.is_mock or flow_resp.is_mock
        source_mode = "mock" if all_mock else ("mixed" if any_mock else "live")

        # 2. Fusion Skill
        fusion_skill = FusionSkill()
        fusion_result = fusion_skill.execute(FusionInput(
            asset=asset,
            question=payload.question,
            claims=claims,
            events=events,
            market_state=market_state,
            flow_signals=flow_signals,
            data_coverage=data_coverage,  # A3: 向 Fusion 传递数据覆盖信号
        ))

        # 3. Briefing Skill
        briefing_skill = BriefingSkill()
        briefing_output = briefing_skill.execute(BriefingInput(
            question=payload.question,
            asset=asset,
            fusion_result=fusion_result,
            time_horizon=payload.time_horizon,
            history=[{"role": h.role, "content": h.content} for h in (payload.history or [])],
            market_state=market_state,
            flow_signals=flow_signals,
            events=events,
        ))
        evidence_skeleton = _apply_selected_source_evidence_skeleton(
            briefing_output,
            question=payload.question,
            source_ids=source_ids,
            claims=claims,
        )

        # 3b. 自动 StrategyMatching — 找最匹配的策略并写入 strategy_fit_summary
        strategy_fit_summary = briefing_output.strategy_fit_summary
        best_setup_id = None
        try:
            from app.schemas.strategy_card import StrategyCard
            from app.skills.strategy_matching_skill import StrategyMatchingSkill, StrategyMatchingInput
            active_strategies = db.query(StrategyCard).filter(
                StrategyCard.status == "active",
            ).all()
            # SQLite JSON contains 可能不精确，在 Python 层二次过滤
            active_strategies = [
                s for s in active_strategies
                if asset in (s.applicable_assets or [])
            ][:5]

            if active_strategies:
                match_skill = StrategyMatchingSkill()
                best_score = 0.0
                best_strategy = None
                for stg in active_strategies:
                    m = match_skill.evaluate(StrategyMatchingInput(
                        strategy_id=stg.id,
                        strategy_name=stg.strategy_name,
                        strategy_type=stg.strategy_type,
                        entry_conditions=stg.entry_conditions,
                        disqualifying_conditions=stg.disqualifying_conditions or [],
                        asset=asset,
                        claims=claims,
                        events=events,
                        market_state=market_state,
                        flow_signals=flow_signals,
                    ))
                    if m.overall_score > best_score:
                        best_score = m.overall_score
                        best_strategy = stg
                        best_match = m

                if best_strategy and best_score >= 0.3:
                    from app.schemas.setup_card import SetupCard
                    setup_now = datetime.now(timezone.utc)
                    strategy_guardrail = build_structured_decision_guardrail(
                        mode="strategy_match",
                        recommended_action=best_match.recommended_action,
                        evidence_count=len(claims) + len(events) + (1 if market_state else 0) + len(flow_signals),
                        source_mode=source_mode,
                        warnings=best_match.disqualifying_flags,
                        is_mock=all_mock,
                        confidence=best_match.overall_score,
                    )
                    setup = SetupCard(
                        id=str(ULID()),
                        strategy_id=best_strategy.id,
                        asset=asset,
                        direction=best_match.direction,
                        strategy_fit_score=best_match.strategy_fit_score,
                        evidence_strength_score=best_match.evidence_strength_score,
                        overall_score=best_match.overall_score,
                        matched_conditions=best_match.matched_conditions,
                        unmatched_conditions=best_match.unmatched_conditions,
                        disqualifying_flags=best_match.disqualifying_flags,
                        recommended_action=strategy_guardrail["effective_action"],
                        action_notes=best_match.action_notes,
                        invalidation_conditions=best_match.invalidation_conditions,
                        supporting_claim_ids=[],
                        supporting_event_ids=[e.get("id", "") for e in events if e.get("id")],
                        market_state_snapshot=market_state or {},
                        flow_snapshot=flow_resp.data or {},
                        status="active",
                        evaluated_at=setup_now,
                        created_at=setup_now,
                        updated_at=setup_now,
                    )
                    db.add(setup)
                    db.flush()
                    best_setup_id = setup.id
                    # 保留 LLM 生成的四层战情分析，将策略匹配信息追加到末尾
                    match_suffix = (
                        f"\n\n【策略匹配结果】\n"
                        f"匹配策略：{best_strategy.strategy_name}｜"
                        f"综合得分 {best_match.overall_score:.0%}｜"
                        f"建议：{strategy_guardrail['effective_action']}"
                    )
                    if strategy_guardrail["status"] == "downgraded":
                        match_suffix += f"（护栏降级，原建议：{strategy_guardrail['original_action']}）"
                    if briefing_output.strategy_fit_summary:
                        strategy_fit_summary = briefing_output.strategy_fit_summary + match_suffix
                    else:
                        strategy_fit_summary = match_suffix.strip()
        except Exception as _stg_err:
            # strategy matching 失败：回滚到 savepoint，确保 job 对象和外层事务不受影响
            try:
                db.rollback()
                # 重新 merge job 对象到新事务（让后续 job 状态更新可以继续）
                job = db.merge(job)
            except Exception:
                pass
            from loguru import logger as _log
            _log.warning(f"[Briefing] strategy matching failed (non-fatal): {_stg_err}")

        phase_started = time.perf_counter()
        quality_filters = {
            "evidence_sufficiency": "claim_supported" if claims else "none",
        }
        if evidence_skeleton["applied"]:
            quality_filters["evidence_skeleton"] = evidence_skeleton["version"]
            quality_filters["evidence_skeleton_conflict"] = evidence_skeleton["conflict"]
        (
            briefing_field_names,
            briefing_field_values,
            preliminary_field_evidence,
        ) = _build_briefing_field_evidence(
            briefing_output,
            quality_filters=quality_filters,
            source_ids=source_ids,
            claims=claims,
            events=events,
        )
        pruned_supporting_point_count = _prune_unverified_supporting_points(
            briefing_output,
            preliminary_field_evidence,
        )
        if pruned_supporting_point_count:
            (
                briefing_field_names,
                briefing_field_values,
                preliminary_field_evidence,
            ) = _build_briefing_field_evidence(
                briefing_output,
                quality_filters=quality_filters,
                source_ids=source_ids,
                claims=claims,
                events=events,
            )
        attribution_warnings = field_evidence_warnings(preliminary_field_evidence)
        generation_warnings = []
        if pruned_supporting_point_count:
            generation_warnings.append(
                {
                    "code": "briefing_unverified_supporting_points_pruned",
                    "message": (
                        f"已隐藏 {pruned_supporting_point_count} 条无法映射到具体证据的支持点。"
                    ),
                    "severity": "warning",
                }
            )
        if briefing_output.error:
            generation_warnings.append(
                {
                    "code": "briefing_generation_fallback",
                    "message": "简报模型输出未通过结构校验，当前结果来自保守兜底。",
                    "severity": "error",
                }
            )
        briefing_guardrail = build_structured_decision_guardrail(
            mode="briefing",
            recommended_action=briefing_output.recommended_action,
            evidence_count=len(claims) + len(events) + (1 if market_state else 0) + len(flow_signals),
            source_mode=source_mode,
            warnings=[
                *attribution_warnings,
                *(item for item in generation_warnings if item.get("severity") == "error"),
            ],
            is_mock=briefing_output.is_mock or all_mock,
        )

        # 4. 持久化 BriefingCard
        briefing = BriefingCard(
            id=str(ULID()),
            title=briefing_output.title,
            topic=briefing_output.topic,
            question=payload.question,
            direction=briefing_output.direction,
            setup_quality=briefing_output.setup_quality,
            time_horizon=briefing_output.time_horizon,
            asset_scope=payload.asset_scope or [asset],
            executive_summary=briefing_output.executive_summary,
            supporting_points=briefing_output.supporting_points,
            contrary_points=briefing_output.contrary_points,
            key_risks=briefing_output.key_risks,
            strategy_fit_summary=strategy_fit_summary,
            recommended_action=briefing_guardrail["effective_action"],
            invalidation_conditions=briefing_output.invalidation_conditions,
            source_claim_ids=[c.get("id", "") for c in claims if c.get("id")],
            source_event_ids=[e.get("id", "") for e in events if e.get("id")],
            setup_id=best_setup_id,
            model_used=briefing_output.model_used,
            committee_level=payload.committee_level,
            status="active",
            generated_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(briefing)
        db.flush()
        create_briefing_decision_snapshot(
            db,
            briefing=briefing,
            question=payload.question,
            asset=asset,
            claims=claims,
            events=events,
            market_state=market_state,
            flow_signals=flow_signals,
            data_coverage=data_coverage,
            decision_guardrail=briefing_guardrail,
            source_mode=source_mode,
            source_ids=source_ids,
        )

        # G3: 更新 job_run 为完成状态
        quality_elapsed_ms = int((time.perf_counter() - started_monotonic) * 1000)
        llm_trace = public_llm_trace(_cs_scope.to_dict())
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.duration_ms = quality_elapsed_ms
        job.related_object_id = briefing.id
        quality_report = build_structured_quality_report(
            decision_ready=briefing_guardrail.get("decision_ready") is True,
            quality_filters=quality_filters,
            claims_used=len(claims),
            events_used=len(events),
            has_market=bool(market_state),
            flow_signals_used=len(flow_signals),
            warnings=generation_warnings,
            duration_ms=quality_elapsed_ms,
            source_mode=source_mode,
            setup_quality=briefing_output.setup_quality,
            field_names=briefing_field_names,
            source_ids=source_ids,
            claim_ids=[c.get("id", "") for c in claims if c.get("id")],
            event_ids=[e.get("id", "") for e in events if isinstance(e, dict) and e.get("id")],
            field_values=briefing_field_values,
            claim_catalog=claims,
            inference_fields=["supporting_points"],
            basis_fields=["direction", "executive_summary"],
            scope_fields=["contrary_points"],
            model_fields=["key_risks"],
            policy_fields=["recommended_action"],
        )
        job.result_summary = {
            "briefing_id": briefing.id,
            "direction": briefing_output.direction,
            "setup_quality": briefing_output.setup_quality,
            "claims_used": len(claims),
            "events_used": len(events),
            "is_mock": briefing_output.is_mock,
            "source_mode": source_mode,
            "duration_ms": quality_elapsed_ms,
            # A3: 累计整次 briefing 的所有 LLM 调用成本（Fusion + Briefing + 可能的 StrategyMatching）
            "cost_usd": round(_cs_scope.total_cost_usd, 6),
            "llm_call_count": _cs_scope.call_count,
            "model_used": briefing_output.model_used,
            "decision_guardrail": briefing_guardrail,
            "llm_trace": llm_trace,
            "quality_report": quality_report,
            "scope": response_scope,
            "quality_filters": quality_filters,
            "route_identity": route_identity,
        }
        job.updated_at = datetime.now(timezone.utc)
        from app.services.decision_trace_service import record_decision_trace_bundle, record_trace_event
        stance_distribution: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0, "conditional": 0}
        retrieval_paths: dict[str, int] = {}
        for claim in claims:
            stance = str(claim.get("stance") or "neutral")
            if stance not in stance_distribution:
                stance = "conditional"
            stance_distribution[stance] += 1
            for path in claim.get("_retrieval_paths") or []:
                retrieval_paths[str(path)] = retrieval_paths.get(str(path), 0) + 1
        record_trace_event(
            db,
            related_object_type="briefing",
            related_object_id=briefing.id,
            event_type="retrieval",
            step_name="briefing_context",
            status="ok" if claims or events or market_state or flow_signals else "warning",
            job_id=job.id,
            source_ids=source_ids,
            payload={
                "scope_mode": response_scope["mode"],
                "scope_resolved_from": "request_source_ids" if source_ids else "asset_scope",
                "search_tier": "briefing_mixed",
                "claims_count": len(claims),
                "events_count": len(events),
                "has_market": bool(market_state),
                "flow_signals_count": len(flow_signals),
                "context_items": len(claims) + len(events) + (1 if market_state else 0) + len(flow_signals),
                "stance_distribution": stance_distribution,
                "retrieval_paths": retrieval_paths,
                "source_scope": response_scope,
                "data_coverage": data_coverage,
            },
        )
        record_decision_trace_bundle(
            db,
            related_object_type="briefing",
            related_object_id=briefing.id,
            job_id=job.id,
            source_ids=source_ids,
            scope=response_scope,
            quality_report=quality_report,
            decision_guardrail=briefing_guardrail,
            llm_trace=llm_trace,
        )
        phase_timings["persistence_setup_ms"] = int(
            (time.perf_counter() - phase_started) * 1000
        )
        duration_ms = int((time.perf_counter() - started_monotonic) * 1000)
        timing_trace = build_timing_trace(
            total_ms=duration_ms,
            llm_calls=llm_trace.get("calls") if isinstance(llm_trace.get("calls"), list) else [],
            phase_timings=phase_timings,
        )
        quality_report["duration_ms"] = duration_ms
        job.duration_ms = duration_ms
        job.result_summary = {
            **(job.result_summary or {}),
            "duration_ms": duration_ms,
            "timing_trace": timing_trace,
            "quality_report": quality_report,
        }
        safe_commit(db)
        db.refresh(briefing)

        _resp_payload = {
            "id": briefing.id,
            "title": briefing.title,
            "topic": briefing.topic,
            "direction": briefing.direction,
            "setup_quality": briefing.setup_quality,
            "executive_summary": briefing.executive_summary,
            "supporting_points": briefing.supporting_points,
            "contrary_points": briefing.contrary_points,
            "key_risks": briefing.key_risks,
            "strategy_fit_summary": strategy_fit_summary,
            "recommended_action": briefing.recommended_action,
            "decision_guardrail": briefing_guardrail,
            "quality_report": quality_report,
            "invalidation_conditions": briefing.invalidation_conditions,
            "is_mock": briefing_output.is_mock,
            "source_mode": source_mode,          # G2
            "scope": response_scope,
            "quality_filters": quality_filters,
            "claims_used": len(claims),
            "source_claim_ids": briefing.source_claim_ids,
            "data_freshness": "simulated" if source_mode == "mock" else ("mixed" if source_mode == "mixed" else "fresh"),  # G2
            "job_id": job.id,                    # G3
            "duration_ms": duration_ms,          # G3
            "timing_trace": timing_trace,
            # A3: 用 cost_scope 累计，不是最后一次的 get_last_usage
            "cost_usd": round(_cs_scope.total_cost_usd, 6),
            "llm_call_count": _cs_scope.call_count,
            "llm_trace": llm_trace,
            "route_identity": route_identity,
            "created_at": _briefing_created_at(briefing.created_at),
            # ── 数据溯源详情 ──────────────────────────────────
            "model_used": briefing_output.model_used,
            "data_sources": {
                "claims": {
                    "is_mock": False,
                    "count": len(claims),
                    "items": [
                        {"summary": c.get("summary", "")[:80], "stance": c.get("stance", ""), "confidence": c.get("confidence", 0)}
                        for c in claims[:5]
                    ],
                },
                "events": {
                    "is_mock": events_resp.is_mock,
                    "count": len(events),
                    "items": [
                        {"title": e.get("title", e.get("summary", ""))[:80], "source": e.get("source", "")}
                        for e in events[:5]
                    ],
                },
                "market": {
                    "is_mock": market_resp.is_mock,
                    "asset": asset,
                    "snapshot": {
                        k: v for k, v in (market_state or {}).items()
                        if k in ("price", "rsi_14", "change_24h", "volume_24h", "ma_20", "ma_50")
                    } if market_state else {},
                },
                "flow": {
                    "is_mock": flow_resp.is_mock,
                    "signals_count": len(flow_signals),
                    "snapshot": {
                        s["signal_type"]: s.get("raw_value")
                        for s in flow_signals
                        if s.get("signal_type") in ("funding_rate", "long_short_ratio", "open_interest", "taker_buy_sell_ratio", "top_trader_long_short")
                    },
                },
            },
        }
        # A3: 成功返回前清理 cost_scope token
        _cs_ctx.reset(_cs_token)
        return _resp_payload

    except Exception as e:
        # A3: 异常路径也要清理 cost_scope token（避免 contextvars 泄漏）
        try:
            _cs_ctx.reset(_cs_token)
        except Exception:
            pass
        # G3: 在干净事务中持久化失败审计，不向 API 或审计字段暴露供应商原文。
        duration_ms = int((time.time() - start_ts) * 1000)
        error_type = type(e).__name__
        import traceback as _traceback
        from loguru import logger as _failure_log

        _frames = _traceback.extract_tb(e.__traceback__)
        _failure_site = (
            f"{_frames[-1].filename}:{_frames[-1].lineno}"
            if _frames
            else "unknown"
        )
        _failure_log.error(
            f"[Briefing] generation failed: {error_type} at {_failure_site}"
        )
        error_code = f"briefing_generation_failed:{error_type}"
        job_id = job.id
        try:
            db.rollback()
        except Exception:
            pass
        failed_job = db.get(JobRun, job_id)
        if failed_job is None:
            failed_job = JobRun(
                id=job_id,
                job_type="briefing_gen",
                status="failed",
                triggered_by="api",
                input_params={
                    "question": payload.question,
                    "asset": asset,
                    "asset_scope": payload.asset_scope,
                    "source_ids": source_ids,
                    "committee_level": payload.committee_level,
                },
                result_summary={},
                source_ids=source_ids,
                object_scope={"asset": asset, "question": payload.question, "source_ids": source_ids},
                started_at=now,
                retry_count=0,
                related_object_type="briefing",
                created_at=now,
                updated_at=now,
            )
            db.add(failed_job)
        failed_job.status = "failed"
        failed_job.completed_at = datetime.now(timezone.utc)
        failed_job.duration_ms = duration_ms
        failed_job.error_message = error_code
        failed_job.result_summary = {
            "status": "failed",
            "error_code": "briefing_generation_failed",
            "error_type": error_type,
            "duration_ms": duration_ms,
            "scope": {"mode": "selected_sources" if source_ids else "global", "source_ids": source_ids},
            "route_identity": route_identity,
        }
        failed_job.updated_at = datetime.now(timezone.utc)
        try:
            safe_commit(db)
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        raise HTTPException(
            status_code=500,
            detail={"code": "briefing_generation_failed", "job_id": job_id},
        )


@router.post("/plan-and-research", response_model=dict)
def plan_and_research(
    request: BriefingRequest = Body(...),
    db: Session = Depends(get_db),
):
    """三阶段深度研究流程：
    阶段一（Plan）   — AI 分析问题，明确数据需求 + 初步假设
    阶段二（Fetch）  — 按需调取 MCP 实时数据
    阶段三（Deep）   — AI 拿到真实数据，深度推演生成完整简报
    """
    from app.skills.research_plan_skill import ResearchPlanSkill
    from app.utils.asset_alias import detect_asset, canonicalize
    from app.utils.cost_scope import CostScope
    from app.utils.cost_scope import _active_scopes as _cs_ctx

    source_ids = clean_source_ids(request.source_ids)

    # ── 资产识别（A1：统一调用 asset_alias.detect_asset）──
    if request.asset_scope:
        asset = canonicalize(request.asset_scope[0]) or "GENERAL"
    else:
        asset = detect_asset(request.question, default="GENERAL")

    # A2: 用 cost_scope 累计 Plan + Fusion + Briefing 三阶段所有 LLM 调用成本
    # 手动 token 管理确保异常路径也能清理；读取时用 _cs_scope.total_cost_usd
    _cs_scope = CostScope(tag="plan_and_research")
    _cs_token = _cs_ctx.set(_cs_ctx.get() + [_cs_scope])

    history = []
    if hasattr(request, 'history') and request.history:
        for h in request.history:
            if isinstance(h, dict):
                history.append({'role': h.get('role', 'user'), 'content': str(h.get('content', ''))[:500]})
            else:
                history.append({'role': getattr(h, 'role', 'user'), 'content': str(getattr(h, 'content', ''))[:500]})
    start_ts = time.time()

    # ══════════════════════════════════════════════
    # 阶段一：研究计划（AI 分析需要哪些数据）
    # ══════════════════════════════════════════════
    plan_skill = ResearchPlanSkill()
    plan = plan_skill.execute(question=request.question or "", history=history)
    # 用 plan 识别的资产覆盖（更准确）
    if plan.asset and plan.asset != "BTC":
        asset = plan.asset

    # ══════════════════════════════════════════════
    # 阶段二：按需调取实时数据
    # ══════════════════════════════════════════════
    assets_to_fetch = [asset] + (plan.secondary_assets or [])
    assets_to_fetch = list(dict.fromkeys(assets_to_fetch))[:3]  # 去重，最多3个

    market_state = None
    flow_signals = []
    events = []
    claims = []
    macro_data = {}

    # ── 私域知识库（始终调取，开放式问题用全库语义RAG）────────
    mem_mcp = ResearchMemoryMCP(db)
    if plan.is_open_ended or plan.asset == 'MACRO':
        # 开放式问题：全库语义搜索，不绑定资产
        rag_resp = mem_mcp.query_for_briefing(
            query=request.question or "",
            asset=None,  # 不过滤资产
            limit=12,
            source_ids=source_ids,
        )
        if rag_resp.success and rag_resp.data:
            raw = rag_resp.data
            claims = raw if isinstance(raw, list) else raw.get('claims', [])
            if claims and isinstance(claims[0], dict):
                pass  # already dicts
            else:
                claims = [
                    {"id": getattr(c, 'id', ''), "summary": getattr(c, 'summary', ''),
                     "stance": getattr(c, 'stance', ''), "time_horizon": getattr(c, 'time_horizon', ''),
                     "trust_score": getattr(c, 'trust_score', 0.5), "confidence": getattr(c, 'confidence', 0.5)}
                    for c in claims
                ]
    else:
        # 资产类问题：用 query_for_briefing 混合召回
        rag_resp = mem_mcp.query_for_briefing(
            query=request.question or "",
            asset=asset,
            limit=10,
            source_ids=source_ids,
        )
        if rag_resp.success and rag_resp.data:
            raw = rag_resp.data
            claims = raw if isinstance(raw, list) else raw.get('claims', [])
            if claims and not isinstance(claims[0], dict):
                claims = [
                    {"id": getattr(c, 'id', ''), "summary": getattr(c, 'summary', ''),
                     "stance": getattr(c, 'stance', ''), "time_horizon": getattr(c, 'time_horizon', ''),
                     "trust_score": getattr(c, 'trust_score', 0.5), "confidence": getattr(c, 'confidence', 0.5)}
                    for c in claims
                ]

    # 市场数据（按需，开放式问题跳过）
    if not source_ids and plan.data_needs.market_data and not plan.is_open_ended and plan.asset != 'MACRO':
        mkt_mcp = MarketDataMCP(db)
        for a in assets_to_fetch:
            r = mkt_mcp.get_market_state(asset=a, timeframe="1d")
            if r.success and r.data:
                d = r.data if isinstance(r.data, dict) else vars(r.data)
                market_state = {
                    "asset": a,
                    "price": d.get("price"),
                    "change_24h": d.get("price_change_1d"),
                    "rsi_14": d.get("rsi_14"),
                    "ma_20": d.get("ma_20"),
                    "ma_50": d.get("ma_50"),
                    "atr_14": d.get("atr_14"),
                    "volume_24h": d.get("volume_vs_avg"),
                    "trend_direction": d.get("trend_direction"),
                    "data_source": d.get("data_source"),
                }
                break  # 主资产数据拿到就够

    # 宏观数据（开放式问题或 plan.data_needs.macro_data 时调取）
    if not source_ids and (plan.data_needs.macro_data or plan.is_open_ended or plan.asset == 'MACRO'):
        try:
            mkt_mcp = MarketDataMCP(db)
            # 拉取宏观关联资产：DXY + SPX + VIX + GOLD
            macro_assets = ['DXY', 'SPX', 'VIX', 'GOLD']
            macro_snapshots = {}
            for ma in macro_assets:
                r = mkt_mcp.get_market_state(asset=ma, timeframe="1d")
                if r.success and r.data:
                    d = r.data if isinstance(r.data, dict) else vars(r.data)
                    macro_snapshots[ma] = {
                        "price": d.get("price"),
                        "change_24h": d.get("price_change_1d"),
                        "rsi_14": d.get("rsi_14"),
                    }
            if macro_snapshots:
                macro_data = macro_snapshots
                # 将宏观数据合并进 market_state 供 LLM 使用
                market_state = {"macro_overview": macro_snapshots}
        except Exception as _macro_err:
            from loguru import logger as _log
            _log.warning(f"[PlanResearch] macro data fetch failed: {_macro_err}")

    # 资金流（按需，开放式问题跳过）
    if not source_ids and plan.data_needs.flow_signals and not plan.is_open_ended and plan.asset != 'MACRO':
        flow_mcp = FlowContinuityMCP(db)
        flow_resp = flow_mcp.get_flow_signals(asset=asset)
        if flow_resp.success and flow_resp.data:
            raw = flow_resp.data if isinstance(flow_resp.data, list) else [flow_resp.data]
            flow_signals = [{"signal_type": s.get("signal_type", "") if isinstance(s, dict) else getattr(s, "signal_type", ""), "raw_value": s.get("raw_value", "") if isinstance(s, dict) else getattr(s, "raw_value", ""), "description": s.get("description", "") if isinstance(s, dict) else getattr(s, "description", "")} for s in raw]

    # 新闻事件（按需）
    if not source_ids and plan.data_needs.news_events:
        evt_mcp = EventFeedMCP(db)
        evt_resp = evt_mcp.fetch_recent_events(asset=asset if not plan.is_open_ended else None, limit=8)
        if evt_resp.success and evt_resp.data:
            raw_events = evt_resp.data if isinstance(evt_resp.data, list) else [evt_resp.data]
            events = [{"id": e.get("id", "") if isinstance(e, dict) else getattr(e, "id", ""), "title": e.get("title", "") if isinstance(e, dict) else getattr(e, "title", ""), "summary": e.get("summary", "") if isinstance(e, dict) else getattr(e, "summary", ""), "importance": e.get("importance", 0) if isinstance(e, dict) else getattr(e, "importance", 0), "source": e.get("source_name", "") if isinstance(e, dict) else getattr(e, "source_name", "")} for e in raw_events]

    # Web 搜索仅用于全局研究；选定资料模式必须保持封闭证据边界。
    web_results = []
    if not source_ids:
        try:
            from app.mcps.web_search_mcp import WebSearchMCP
            web_mcp = WebSearchMCP()
            if plan.is_open_ended or plan.asset == 'MACRO':
                ws_resp = web_mcp.search_macro(request.question or "", max_results=6)
            else:
                ws_resp = web_mcp.search_news(topic=request.question or "", asset=asset, max_results=5)
            if ws_resp.success and ws_resp.data:
                web_results = ws_resp.data
                # 将 web 结果也注入 events 列表（方便 FusionSkill 统一处理）
                for wr in web_results:
                    events.append({
                        "id": "",
                        "title": wr.get("title", ""),
                        "summary": wr.get("snippet", ""),
                        "importance": wr.get("score", 0.5),
                        "source": f"web:{wr.get('source', 'search')}",
                        "url": wr.get("url", ""),
                    })
        except Exception as _ws_err:
            from loguru import logger as _log
            _log.warning(f"[PlanResearch] web search failed (non-fatal): {_ws_err}")

    # ══════════════════════════════════════════════
    # 阶段三：深度推演（Fusion + Briefing）
    # ══════════════════════════════════════════════
    fusion_skill = FusionSkill()
    fusion_input = FusionInput(
        question=request.question or "",
        asset=asset,
        claims=claims,
        events=events,
        market_state=market_state or {},
        flow_signals=flow_signals,
        time_horizon=request.time_horizon,
        history=history,
    )
    fusion_output = fusion_skill.execute(fusion_input)

    briefing_skill = BriefingSkill()
    briefing_output = briefing_skill.execute(BriefingInput(
        question=request.question or "",
        asset=asset,
        fusion_result=fusion_output,
        time_horizon=request.time_horizon,
        history=history,
        market_state=market_state,
        flow_signals=flow_signals,
        events=events,
    ))
    evidence_skeleton = _apply_selected_source_evidence_skeleton(
        briefing_output,
        question=request.question or "",
        source_ids=source_ids,
        claims=claims,
    )

    duration_ms = int((time.time() - start_ts) * 1000)
    source_mode = "live" if (market_state or claims or events or web_results or flow_signals) else "mock"
    llm_trace = public_llm_trace(_cs_scope.to_dict())
    quality_filters = {
        "evidence_sufficiency": (
            "claim_supported"
            if claims
            else "source_supported"
            if (events or market_state or flow_signals or web_results)
            else "none"
        ),
        "claims_used": len(claims),
        "events_used": len(events),
        "web_results_used": len(web_results),
        "source_mode": source_mode,
    }
    if evidence_skeleton["applied"]:
        quality_filters["evidence_skeleton"] = evidence_skeleton["version"]
        quality_filters["evidence_skeleton_conflict"] = evidence_skeleton["conflict"]
    (
        briefing_field_names,
        briefing_field_values,
        preliminary_field_evidence,
    ) = _build_briefing_field_evidence(
        briefing_output,
        quality_filters=quality_filters,
        source_ids=source_ids,
        claims=claims,
        events=events,
    )
    pruned_supporting_point_count = _prune_unverified_supporting_points(
        briefing_output,
        preliminary_field_evidence,
    )
    if pruned_supporting_point_count:
        (
            briefing_field_names,
            briefing_field_values,
            preliminary_field_evidence,
        ) = _build_briefing_field_evidence(
            briefing_output,
            quality_filters=quality_filters,
            source_ids=source_ids,
            claims=claims,
            events=events,
        )
    attribution_warnings = field_evidence_warnings(preliminary_field_evidence)
    generation_warnings = []
    if pruned_supporting_point_count:
        generation_warnings.append(
            {
                "code": "briefing_unverified_supporting_points_pruned",
                "message": (
                    f"已隐藏 {pruned_supporting_point_count} 条无法映射到具体证据的支持点。"
                ),
                "severity": "warning",
            }
        )
    if briefing_output.error:
        generation_warnings.append(
            {
                "code": "briefing_generation_fallback",
                "message": "简报模型输出未通过结构校验，当前结果来自保守兜底。",
                "severity": "error",
            }
        )
    briefing_guardrail = build_structured_decision_guardrail(
        mode="briefing_research",
        recommended_action=briefing_output.recommended_action,
        evidence_count=len(claims) + len(events) + (1 if market_state else 0) + len(flow_signals) + len(web_results),
        source_mode=source_mode,
        warnings=[
            *attribution_warnings,
            *(item for item in generation_warnings if item.get("severity") == "error"),
        ],
        is_mock=briefing_output.is_mock and source_mode == "mock",
        confidence=plan.preliminary_conviction,
    )
    quality_report = build_structured_quality_report(
        decision_ready=briefing_guardrail.get("decision_ready") is True,
        quality_filters=quality_filters,
        claims_used=len(claims),
        events_used=len(events),
        has_market=bool(market_state),
        flow_signals_used=len(flow_signals),
        warnings=generation_warnings,
        duration_ms=duration_ms,
        source_mode=source_mode,
        setup_quality=briefing_output.setup_quality,
        extra_evidence={"web_results_used": len(web_results)},
        field_names=briefing_field_names,
        source_ids=source_ids,
        claim_ids=[c.get("id", "") for c in claims if isinstance(c, dict) and c.get("id")],
        event_ids=[e.get("id", "") for e in events if isinstance(e, dict) and e.get("id")],
        field_values=briefing_field_values,
        claim_catalog=claims,
        inference_fields=["supporting_points"],
        basis_fields=["direction", "executive_summary"],
        scope_fields=["contrary_points"],
        model_fields=["key_risks"],
        policy_fields=["recommended_action"],
    )

    _return_payload = {
        "scope": {
            "mode": "selected_sources" if source_ids else "global",
            "source_ids": source_ids,
            "asset": asset,
        },
        "data_sources": {
            "claims": {"count": len(claims)},
            "events": {"count": len(events)},
            "market": {"asset": asset, "snapshot": market_state or {}},
            "flow": {"signals_count": len(flow_signals)},
            "web": {"count": len(web_results)},
        },
        "plan": {
            "asset": plan.asset,
            "is_open_ended": plan.is_open_ended,
            "secondary_assets": plan.secondary_assets,
            "preliminary_direction": plan.preliminary_direction,
            "preliminary_conviction": plan.preliminary_conviction,
            "key_hypotheses": plan.key_hypotheses,
            "open_questions": plan.open_questions,
            "reasoning": plan.reasoning,
            "model_used": plan.model_used,
        },
        "data_fetched": {
            "market_data": market_state is not None,
            "flow_signals": len(flow_signals),
            "events": len(events),
            "claims": len(claims),
            "web_results": len(web_results),
            "assets_fetched": assets_to_fetch,
            "scope": {
                "mode": "selected_sources" if source_ids else "global",
                "source_ids": source_ids,
            },
            # 详细溯源清单
            "sources": {
                "private_claims": [
                    {"summary": c.get("summary", "")[:100], "stance": c.get("stance", ""),
                     "trust_score": c.get("trust_score", 0), "confidence": c.get("confidence", 0)}
                    for c in claims
                ],
                "market_snapshot": market_state if market_state else None,
                "flow_signals": [
                    {"signal_type": s.get("signal_type", ""), "raw_value": s.get("raw_value", ""),
                     "description": s.get("description", "")}
                    for s in flow_signals
                ],
                "news_events": [
                    {"title": e.get("title", ""), "source": e.get("source", ""),
                     "importance": e.get("importance", 0)}
                    for e in events if not str(e.get("source", "")).startswith("web:")
                ],
                "web_search": [
                    {"title": r.get("title", ""), "url": r.get("url", ""),
                     "snippet": r.get("snippet", "")[:200], "source": r.get("source", ""),
                     "score": r.get("score", 0)}
                    for r in web_results
                ],
            },
        },
        "briefing": {
            "title": briefing_output.title,
            "topic": briefing_output.topic,
            "asset": asset,
            "direction": briefing_output.direction,
            "setup_quality": briefing_output.setup_quality,
            "time_horizon": briefing_output.time_horizon,
            "executive_summary": briefing_output.executive_summary,
            "supporting_points": briefing_output.supporting_points,
            "contrary_points": briefing_output.contrary_points,
            "key_risks": briefing_output.key_risks,
            "strategy_fit_summary": briefing_output.strategy_fit_summary,
            "recommended_action": briefing_guardrail["effective_action"],
            "decision_guardrail": briefing_guardrail,
            "quality_report": quality_report,
            "invalidation_conditions": briefing_output.invalidation_conditions,
            "model_used": briefing_output.model_used,
            "is_mock": briefing_output.is_mock,
            "source_mode": source_mode,
            "quality_filters": quality_filters,
            "duration_ms": duration_ms,
            # A2: 累计整个三阶段的成本（Plan + Fusion + Briefing），而不是只最后一次
            "cost_usd": round(_cs_scope.total_cost_usd, 6),
            "llm_call_count": _cs_scope.call_count,
            "total_input_tokens": _cs_scope.total_input_tokens,
            "total_output_tokens": _cs_scope.total_output_tokens,
            "llm_trace": llm_trace,
        },
    }
    # 在返回之前 reset token（避免 scope 泄漏）
    _cs_ctx.reset(_cs_token)
    return _return_payload


@router.get("/{briefing_id}", response_model=dict)
def get_briefing(
    briefing_id: str,
    db: Session = Depends(get_db),
):
    """获取单个简报"""
    repo = BriefingRepository(db)
    item = repo.get(briefing_id)
    if not item:
        raise HTTPException(status_code=404, detail="Briefing not found")
    job = (
        db.query(JobRun)
        .filter(
            JobRun.job_type == "briefing_gen",
            JobRun.related_object_id == briefing_id,
        )
        .order_by(JobRun.created_at.desc())
        .first()
    )
    job_summary = job.result_summary if job and isinstance(job.result_summary, dict) else {}
    source_claim_ids = job_summary.get("source_claim_ids")
    if not isinstance(source_claim_ids, list):
        source_claim_ids = item.source_claim_ids or []
    claims_used = job_summary.get("claims_used")
    if not isinstance(claims_used, int):
        claims_used = len(source_claim_ids)
    return {
        "id": item.id,
        "title": item.title,
        "topic": item.topic,
        "direction": item.direction,
        "setup_quality": item.setup_quality,
        "executive_summary": item.executive_summary,
        "supporting_points": item.supporting_points,
        "contrary_points": item.contrary_points,
        "key_risks": item.key_risks,
        "recommended_action": item.recommended_action,
        "decision_guardrail": job_summary.get("decision_guardrail"),
        "quality_report": job_summary.get("quality_report") if isinstance(job_summary.get("quality_report"), dict) else None,
        "invalidation_conditions": item.invalidation_conditions,
        "quality_filters": job_summary.get("quality_filters") or {},
        "claims_used": claims_used,
        "source_claim_ids": source_claim_ids,
        "scope": job_summary.get("scope") or {},
        "model_used": item.model_used,
        "duration_ms": job.duration_ms if job else None,
        "timing_trace": job_summary.get("timing_trace") if isinstance(job_summary.get("timing_trace"), dict) else None,
        "cost_usd": job_summary.get("cost_usd"),
        "llm_call_count": job_summary.get("llm_call_count"),
        "llm_trace": public_llm_trace(job_summary.get("llm_trace")),
        "route_identity": job_summary.get("route_identity"),
        "source_mode": job_summary.get("source_mode"),
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
