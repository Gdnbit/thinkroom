"""Committee Workflow

职责：编排委员会分析的完整流程：
1. 创建 DebateRecord
2. V2: CommitteeOrchestrator 动态激活席位（最多 6 席）
3. 多轮分析（按激活席位发言）
4. 调用 FinalAdjudicationSkill 给出裁决
5. 持久化 FinalAdjudication + DecisionSnapshot
6. 更新 DebateRecord 状态

席位设计：
- Baseline Analyst（基准分析师）→ committee_a → Claude Opus
- Probability Analyst（概率分析师）→ committee_b → Gemini Pro
- Risk Sentinel（风控哨兵）→ committee_c → GPT-5.4
- Structure Analyst（市场结构分析师）→ committee_d → GLM-5
- Truth/Audit（真伪审计官）→ committee_e → DeepSeek V3.2 （V2）
- PnL/Execution（执行经济性顾问）→ committee_f → Gemini Flash （V2）
"""
import re
import time
from datetime import datetime, timedelta, timezone
from app.database import safe_commit
from typing import Any, Optional
from sqlalchemy.orm import Session, sessionmaker
from ulid import ULID
from loguru import logger

from app.schemas.debate import (
    DebateRecord,
    DebateRound,
    FinalAdjudication,
    store_adjudication_metadata,
)
from app.schemas.job_run import JobRun
from app.schemas.decision_snapshot import DecisionSnapshot
from app.mcps.research_memory_mcp import ResearchMemoryMCP
from app.mcps.event_feed_mcp import EventFeedMCP
from app.mcps.market_data_mcp import MarketDataMCP
from app.skills.committee_debate_skill import (
    CommitteeDebateSkill,
    DebateRoundInput,
    ROLE_DISPLAY_NAMES,
    ROLE_SKILL_MAP,
)
from app.skills.final_adjudication_skill import FinalAdjudicationSkill, AdjudicationInput
from app.services.committee_orchestrator import CommitteeOrchestrator
from app.services.committee_route_identity import committee_route_identity
from app.utils.decision_guardrail import build_structured_decision_guardrail
from app.utils.cost_scope import cost_scope
from app.utils.public_llm_trace import public_llm_trace
from app.utils.source_scope import clean_source_ids
from app.utils.structured_quality_report import (
    build_field_evidence_contract,
    build_structured_quality_report,
    field_evidence_warnings,
)


# 角色 → 默认模型映射（实际模型由 config.py COMMITTEE_MODEL_A/B/C/D/E/F 决定）
DEFAULT_BASELINE_MODEL = "committee_a"
DEFAULT_PROBABILITY_MODEL = "committee_b"
DEFAULT_RISK_MODEL = "committee_c"
DEFAULT_STRUCTURE_MODEL = "committee_d"
DEFAULT_AUDIT_MODEL = "committee_e"
DEFAULT_PNL_MODEL = "committee_f"
DEFAULT_ADJUDICATOR = "committee_a"
COMMITTEE_FAILURE_CODE = "committee_execution_failed"
COMMITTEE_FAILURE_MESSAGE = "Committee execution failed."
_COMMITTEE_FIELD_NAMES = [
    "direction",
    "recommended_action",
    "synthesis",
    "risk_warnings",
    "structure_warnings",
]

# Deprecated (A6): 旧角色→DB 列名映射；新代码请用 app.utils.debate_seats.get_seat_model
# 保留此字典仅用于外部引用的向后兼容，0010 migration 后会删除。
_ROLE_TO_DB_COLUMN = {
    "baseline": "model_bull",
    "probability": "model_bear",
    "risk": "model_neutral",
    "structure": "model_neutral",  # 第4席复用 model_neutral 列（DB 兼容无迁移）
    "audit": "model_neutral",     # V2 新席复用
    "pnl": "model_neutral",       # V2 新席复用
}

_MATERIAL_CLAIM_TOKEN_RE = re.compile(
    r"20\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|"
    r"[+-]?\d+(?:\.\d+)?\s*(?:%|个百分点|份|个|亿元|万元|美元|人民币|吨|桶|月|季度)"
)


def _material_claim_tokens(text: str) -> set[str]:
    return {
        re.sub(r"\s+", "", token).lstrip("+-").casefold()
        for token in _MATERIAL_CLAIM_TOKEN_RE.findall(text or "")
        if token.strip()
    }


def _ensure_material_claim_anchors(
    synthesis: str,
    claims: list[dict[str, Any]],
    *,
    max_claims: int = 3,
) -> tuple[str, int]:
    """Prepend concise claim text when adjudication drops material numbers or dates."""
    current = str(synthesis or "").strip()
    current_normalized = re.sub(r"\s+", "", current).casefold()
    missing_summaries: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        summary = str(
            claim.get("summary")
            or claim.get("full_quote")
            or claim.get("contextual_text")
            or ""
        ).strip()
        tokens = _material_claim_tokens(summary)
        if not summary or not tokens:
            continue
        if all(token in current_normalized for token in tokens):
            continue
        missing_summaries.append(summary[:180])
        if len(missing_summaries) >= max(1, int(max_claims)):
            break
    if not missing_summaries:
        return current, 0
    anchor = "证据锚点：" + "；".join(missing_summaries).rstrip("。； ") + "。"
    return f"{anchor} {current}".strip(), len(missing_summaries)


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


class CommitteeWorkflow:
    def __init__(self, db: Session):
        self.db = db
        self.debate_skill = CommitteeDebateSkill()
        self.adjudication_skill = FinalAdjudicationSkill()

    @staticmethod
    def _build_timing_trace(
        *,
        total_ms: int,
        llm_calls: list[dict],
        phase_timings: dict[str, int],
    ) -> dict[str, int]:
        phases = {
            key: max(0, int(value))
            for key, value in phase_timings.items()
            if key.endswith("_ms")
        }
        model_calls_ms = sum(
            max(0, int(call.get("duration_ms") or 0))
            for call in llm_calls
            if isinstance(call, dict)
        )
        accounted_ms = sum(phases.values()) + model_calls_ms
        return {
            **phases,
            "model_calls_ms": model_calls_ms,
            "other_processing_ms": max(0, int(total_ms) - accounted_ms),
            "total_ms": max(0, int(total_ms)),
        }

    @staticmethod
    def _minimum_successful_seats(activated_seat_count: int) -> int:
        if activated_seat_count <= 0:
            return 0
        return (activated_seat_count // 2) + 1

    @classmethod
    def _build_seat_quality(
        cls,
        *,
        active_seat_ids: list[str],
        seat_states: dict[str, dict[str, Any]],
        expected_rounds: int,
    ) -> dict[str, Any]:
        seats: dict[str, dict[str, Any]] = {}
        successful_seat_count = 0
        error_seat_count = 0
        mock_seat_count = 0

        for role in active_seat_ids:
            state = seat_states.get(role, {})
            error_rounds = sorted(set(state.get("error_rounds") or []))
            mock_rounds = sorted(set(state.get("mock_rounds") or []))
            successful_rounds = int(state.get("successful_rounds") or 0)
            if error_rounds:
                status = "error"
                error_seat_count += 1
            elif mock_rounds:
                status = "mock"
                mock_seat_count += 1
            elif successful_rounds < expected_rounds:
                status = "incomplete"
                error_seat_count += 1
            else:
                status = "success"
                successful_seat_count += 1

            if mock_rounds and status != "mock":
                mock_seat_count += 1
            seats[role] = {
                "status": status,
                "expected_rounds": expected_rounds,
                "successful_rounds": successful_rounds,
                "error_rounds": error_rounds,
                "mock_rounds": mock_rounds,
            }

        activated_seat_count = len(active_seat_ids)
        minimum_successful_seats = cls._minimum_successful_seats(activated_seat_count)
        return {
            "activated_seat_count": activated_seat_count,
            "minimum_successful_seats": minimum_successful_seats,
            "successful_seat_count": successful_seat_count,
            "error_seat_count": error_seat_count,
            "mock_seat_count": mock_seat_count,
            "quorum_met": successful_seat_count >= minimum_successful_seats,
            "seats": seats,
        }

    @staticmethod
    def _seat_quality_warnings(seat_quality: dict[str, Any]) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        for role, state in (seat_quality.get("seats") or {}).items():
            status = state.get("status")
            label = ROLE_DISPLAY_NAMES.get(role, role)
            if status in {"error", "incomplete"}:
                warnings.append(
                    {
                        "code": "committee_seat_error",
                        "message": f"{label} did not return all required valid rounds.",
                        "severity": "error",
                    }
                )
            if state.get("mock_rounds"):
                warnings.append(
                    {
                        "code": "committee_seat_mock",
                        "message": f"{label} used mock or simulated model output.",
                        "severity": "error",
                    }
                )
        if not seat_quality.get("quorum_met"):
            warnings.append(
                {
                    "code": "committee_quorum_not_met",
                    "message": (
                        "Committee quorum was not met: "
                        f"{seat_quality.get('successful_seat_count', 0)}/"
                        f"{seat_quality.get('minimum_successful_seats', 0)} successful seats."
                    ),
                    "severity": "error",
                }
            )
        return warnings

    def _persist_failed_run(
        self,
        *,
        debate_id: str,
        job_id: str,
        question: str,
        asset: str,
        num_rounds: int,
        source_ids: list[str],
        started_at: datetime,
        duration_ms: int,
        model_by_seat: dict[str, str],
        context: dict[str, Any],
        route_identity: dict[str, Any],
        llm_traces: list[dict],
        total_cost: float,
        seat_quality: dict[str, Any] | None,
        error: Exception,
    ) -> bool:
        """Persist failure audit rows in a fresh session after the main rollback."""
        try:
            self.db.rollback()
        except Exception:
            pass

        failed_at = datetime.now(timezone.utc)
        safe_error = COMMITTEE_FAILURE_MESSAGE
        retry_count = sum(
            1 for call in llm_traces if int(call.get("schema_attempt") or 1) > 1
        )
        llm_trace = public_llm_trace({
            "call_count": len(llm_traces),
            "total_cost_usd": round(total_cost, 6),
            "calls": llm_traces,
        })
        result_summary = {
            "debate_id": debate_id,
            "cost_usd": round(total_cost, 6),
            "llm_trace": llm_trace,
            "scope": context.get("scope", {}) if isinstance(context, dict) else {},
            "route_identity": route_identity,
            "seat_quality": seat_quality or {},
            "failure": {
                "code": COMMITTEE_FAILURE_CODE,
                "error_type": type(error).__name__,
            },
        }

        failure_session_factory = sessionmaker(
            bind=self.db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
        failure_db = failure_session_factory()
        try:
            failed_debate = failure_db.get(DebateRecord, debate_id)
            if failed_debate is None:
                failed_debate = DebateRecord(
                    id=debate_id,
                    question=question,
                    asset=asset,
                    committee_level="triple",
                    model_by_seat=dict(model_by_seat or {}),
                    model_bull=(model_by_seat or {}).get("baseline"),
                    model_bear=(model_by_seat or {}).get("probability"),
                    model_neutral=(model_by_seat or {}).get("risk"),
                    status="failed",
                    context_claims=list(context.get("claims") or []),
                    context_events=list(context.get("events") or []),
                    context_market=dict(context.get("market") or {}),
                    started_at=started_at,
                    completed_at=failed_at,
                    duration_ms=duration_ms,
                    error_message=safe_error,
                    created_at=started_at,
                    updated_at=failed_at,
                )
                failure_db.add(failed_debate)
            else:
                failed_debate.status = "failed"
                failed_debate.completed_at = failed_at
                failed_debate.duration_ms = duration_ms
                failed_debate.error_message = safe_error
                failed_debate.updated_at = failed_at

            failed_job = failure_db.get(JobRun, job_id)
            if failed_job is None:
                failed_job = JobRun(
                    id=job_id,
                    job_type="committee_debate",
                    status="failed",
                    triggered_by="api",
                    input_params={
                        "question": question,
                        "asset": asset,
                        "num_rounds": num_rounds,
                        "source_ids": source_ids,
                    },
                    result_summary=result_summary,
                    source_ids=source_ids,
                    object_scope={
                        "asset": asset,
                        "question": question,
                        "source_ids": source_ids,
                    },
                    started_at=started_at,
                    completed_at=failed_at,
                    duration_ms=duration_ms,
                    error_message=safe_error,
                    retry_count=retry_count,
                    related_object_id=debate_id,
                    related_object_type="debate_record",
                    created_at=started_at,
                    updated_at=failed_at,
                )
                failure_db.add(failed_job)
            else:
                failed_job.status = "failed"
                failed_job.result_summary = result_summary
                failed_job.completed_at = failed_at
                failed_job.duration_ms = duration_ms
                failed_job.error_message = safe_error
                failed_job.retry_count = retry_count
                failed_job.updated_at = failed_at

            failure_db.commit()
            return True
        except Exception as persistence_error:
            failure_db.rollback()
            logger.error(
                "[Committee] independent failure audit persistence failed: "
                f"{type(persistence_error).__name__}"
            )
            return False
        finally:
            failure_db.close()

    def _selected_source_insufficiency(
        self,
        *,
        question: str,
        asset: str,
        source_ids: list[str],
    ) -> tuple[str | None, Any | None]:
        if not source_ids:
            return None, None

        from app.services.chat_harness import build_chat_context
        from app.services.selected_scope_sufficiency import selected_scope_insufficiency_reason

        chat_ctx = build_chat_context(
            db=self.db,
            query=question,
            asset_filter=None if asset == "GENERAL" else asset,
            limit=12,
            include_market=False,
            source_ids=source_ids,
            exclude_expired=True,
            exclude_contradicted=True,
        )
        return selected_scope_insufficiency_reason(question, chat_ctx), chat_ctx

    def _complete_insufficient_selected_scope(
        self,
        *,
        question: str,
        asset: str,
        num_rounds: int,
        source_ids: list[str],
        reason: str,
        selected_scope_ctx: Any | None,
        now: datetime,
        start_ts: float,
        phase_timings: dict[str, int],
        route_identity: dict[str, Any],
    ) -> dict:
        from app.services.decision_trace_service import record_decision_trace_bundle, record_trace_event

        source_scope_meta = (
            getattr(selected_scope_ctx, "source_scope", {}) if selected_scope_ctx is not None else {}
        )
        scope = {
            "mode": "selected_sources",
            "source_ids": source_ids,
            "asset": asset,
            "source_scope": source_scope_meta,
        }
        detail = _selected_scope_insufficiency_detail(reason)
        reference_time = now.astimezone(timezone(timedelta(hours=8))).strftime("%Y年%m月%d日 %H:%M:%S CST")
        synthesis = (
            f"截至 {reference_time}，选定资料不足以支持委员会对“{question.strip()[:80]}”作出方向性裁决。"
            f"{detail}"
            "因此本次不启动多席位讨论，不生成上涨、下跌、买入、卖出或加仓等操作结论。"
        )
        configured_routes = route_identity["configuration"]["routes"]
        quality_filters = {
            "evidence_sufficiency": "insufficient",
            "insufficient_evidence_reason": reason,
        }
        decision_guardrail = build_structured_decision_guardrail(
            mode="committee",
            recommended_action="watch",
            evidence_count=0,
            source_mode="insufficient_evidence",
            warnings=[detail],
            is_mock=False,
            confidence=0.0,
        )

        debate = DebateRecord(
            id=str(ULID()),
            question=question,
            asset=asset,
            committee_level="triple",
            model_by_seat={},
            model_bull=configured_routes["baseline"]["primary_model"],
            model_bear=configured_routes["probability"]["primary_model"],
            model_neutral=configured_routes["risk"]["primary_model"],
            status="completed",
            context_claims=[],
            context_events=[],
            context_market={},
            final_direction="neutral",
            final_conviction=0.0,
            final_action=decision_guardrail["effective_action"],
            consensus_level="no_consensus",
            started_at=now,
            completed_at=datetime.now(timezone.utc),
            created_at=now,
            updated_at=now,
        )
        self.db.add(debate)
        self.db.flush()

        job = JobRun(
            id=str(ULID()),
            job_type="committee_debate",
            status="completed",
            triggered_by="api",
            input_params={
                "question": question,
                "asset": asset,
                "num_rounds": num_rounds,
                "source_ids": source_ids,
            },
            result_summary={},
            source_ids=source_ids,
            object_scope={
                "asset": asset,
                "question": question,
                "source_ids": source_ids,
            },
            started_at=now,
            completed_at=datetime.now(timezone.utc),
            related_object_id=debate.id,
            related_object_type="debate_record",
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        self.db.add(job)
        self.db.flush()

        adj = FinalAdjudication(
            id=str(ULID()),
            debate_id=debate.id,
            direction="neutral",
            conviction=0.0,
            recommended_action=decision_guardrail["effective_action"],
            consensus_level="no_consensus",
            synthesis=synthesis,
            bull_score=0.5,
            bear_score=0.5,
            key_agreements=[],
            key_disagreements=[],
            structure_score=0.0,
            structure_warnings=[],
            risk_warnings=[detail],
            hedging_advice="等待补充可支撑该问题的方向性证据后再审议。",
            action_economics={"decision_guardrail": decision_guardrail},
            minority_view=None,
            invalidation_conditions=[
                "补充覆盖目标对象、时间窗口、方向性依据和操作条件的资料后重新运行委员会。"
            ],
            adjudicated_by="not_called_insufficient_evidence",
            is_mock=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(adj)

        duration_ms = int((time.time() - start_ts) * 1000)
        timing_trace = self._build_timing_trace(
            total_ms=duration_ms,
            llm_calls=[],
            phase_timings=phase_timings,
        )
        debate.duration_ms = duration_ms
        job.duration_ms = duration_ms
        llm_trace = public_llm_trace({
            "call_count": 0,
            "total_cost_usd": 0.0,
            "calls": [],
            "model": "not_called_insufficient_evidence",
            "error_type": "insufficient_evidence",
        })
        quality_report = build_structured_quality_report(
            decision_ready=False,
            quality_filters=quality_filters,
            claims_used=0,
            events_used=0,
            has_market=False,
            warnings=[{"code": "insufficient_evidence", "message": detail}],
            duration_ms=duration_ms,
            source_mode="selected_sources",
            setup_quality="insufficient",
            field_names=_COMMITTEE_FIELD_NAMES,
            source_ids=source_ids,
            extra_evidence={
                "activated_seats": [],
                "round_count": 0,
                "consensus_level": "no_consensus",
            },
        )
        job.result_summary = {
            "debate_id": debate.id,
            "direction": "neutral",
            "cost_usd": 0.0,
            "decision_guardrail": decision_guardrail,
            "llm_trace": llm_trace,
            "quality_report": quality_report,
            "scope": scope,
            "quality_filters": quality_filters,
            "timing_trace": timing_trace,
            "route_identity": route_identity,
        }

        record_trace_event(
            self.db,
            related_object_type="debate",
            related_object_id=debate.id,
            event_type="retrieval",
            step_name="committee_context",
            status="warning",
            job_id=job.id,
            source_ids=source_ids,
            payload={
                "scope_mode": "selected_sources",
                "scope_resolved_from": "request_source_ids",
                "search_tier": "selected_source_sufficiency",
                "claims_count": 0,
                "events_count": 0,
                "has_market": False,
                "context_items": 0,
                "source_scope": source_scope_meta or scope,
                "quality_filters": quality_filters,
            },
        )
        record_decision_trace_bundle(
            self.db,
            related_object_type="debate",
            related_object_id=debate.id,
            job_id=job.id,
            source_ids=source_ids,
            scope=scope,
            quality_report=quality_report,
            decision_guardrail=decision_guardrail,
            llm_trace=llm_trace,
        )

        safe_commit(self.db)
        return {
            "success": True,
            "debate_id": debate.id,
            "direction": "neutral",
            "conviction": 0.0,
            "recommended_action": decision_guardrail["effective_action"],
            "consensus_level": "no_consensus",
            "synthesis": synthesis,
            "risk_warnings": [detail],
            "structure_warnings": [],
            "evidence_quality_warnings": [detail],
            "action_economics": {"decision_guardrail": decision_guardrail},
            "hedging_advice": "等待补充可支撑该问题的方向性证据后再审议。",
            "decision_guardrail": decision_guardrail,
            "quality_report": quality_report,
            "quality_filters": quality_filters,
            "activated_seats": [],
            "round_count": 0,
            "is_mock": False,
            "cost_usd": 0.0,
            "llm_trace": llm_trace,
            "scope": scope,
            "data_sources": {
                "claims": {"is_mock": False, "count": 0, "items": []},
                "events": {"is_mock": False, "count": 0, "items": []},
                "market": {"is_mock": False, "asset": asset, "snapshot": {}},
                "flow": {"is_mock": False, "signals_count": 0, "snapshot": {}},
                "agent_retrieval": {"counts": {}},
            },
            "duration_ms": duration_ms,
            "timing_trace": timing_trace,
            "route_identity": route_identity,
        }

    def run(
        self,
        question: str,
        asset: str,
        num_rounds: int = 2,
        baseline_model: Optional[str] = None,
        probability_model: Optional[str] = None,
        risk_model: Optional[str] = None,
        structure_model: Optional[str] = None,
        task_mode: str = "decision",
        force_seats: Optional[list[str]] = None,
        source_ids: Optional[list[str]] = None,
    ) -> dict:
        """Run the committee with a failure-audit boundary around every pre-stage."""
        clean_ids = clean_source_ids(source_ids)
        debate_id = str(ULID())
        job_id = str(ULID())
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.perf_counter()
        try:
            return self._run(
                question=question,
                asset=asset,
                num_rounds=num_rounds,
                baseline_model=baseline_model,
                probability_model=probability_model,
                risk_model=risk_model,
                structure_model=structure_model,
                task_mode=task_mode,
                force_seats=force_seats,
                source_ids=clean_ids,
                _audit_debate_id=debate_id,
                _audit_job_id=job_id,
            )
        except Exception as error:
            model_overrides = {
                role: model
                for role, model in {
                    "baseline": baseline_model,
                    "probability": probability_model,
                    "risk": risk_model,
                    "structure": structure_model,
                }.items()
                if model
            }
            try:
                route_identity = committee_route_identity(model_overrides=model_overrides)
            except Exception:
                route_identity = {}
            routes = (
                route_identity.get("configuration", {}).get("routes", {})
                if isinstance(route_identity, dict)
                else {}
            )
            model_by_seat = {
                role: str(route.get("primary_model") or "")
                for role, route in routes.items()
                if role in ROLE_SKILL_MAP and isinstance(route, dict)
            }
            context = {
                "claims": [],
                "events": [],
                "market": {},
                "scope": {
                    "mode": "selected_sources" if clean_ids else "global",
                    "source_ids": clean_ids,
                },
            }
            self._persist_failed_run(
                debate_id=debate_id,
                job_id=job_id,
                question=question,
                asset=asset,
                num_rounds=num_rounds,
                source_ids=clean_ids,
                started_at=started_at,
                duration_ms=int((time.perf_counter() - started_monotonic) * 1000),
                model_by_seat=model_by_seat,
                context=context,
                route_identity=route_identity,
                llm_traces=[],
                total_cost=0.0,
                seat_quality=None,
                error=error,
            )
            logger.error(f"[Committee] pre-stage failed: {type(error).__name__}")
            return {
                "success": False,
                "error": COMMITTEE_FAILURE_MESSAGE,
                "error_code": COMMITTEE_FAILURE_CODE,
                "debate_id": debate_id,
                "job_id": job_id,
            }

    def _run(
        self,
        question: str,
        asset: str,
        num_rounds: int = 2,
        baseline_model: Optional[str] = None,
        probability_model: Optional[str] = None,
        risk_model: Optional[str] = None,
        structure_model: Optional[str] = None,
        task_mode: str = "decision",
        force_seats: Optional[list[str]] = None,
        source_ids: Optional[list[str]] = None,
        _audit_debate_id: str | None = None,
        _audit_job_id: str | None = None,
    ) -> dict:
        """运行完整的委员会分析流程"""
        source_ids = clean_source_ids(source_ids)
        now = datetime.now(timezone.utc)
        start_ts = time.time()
        phase_timings: dict[str, int] = {}
        model_overrides = {
            role: model
            for role, model in {
                "baseline": baseline_model,
                "probability": probability_model,
                "risk": risk_model,
                "structure": structure_model,
            }.items()
            if model
        }
        route_identity = committee_route_identity(model_overrides=model_overrides)

        # 实时进度追踪
        from app.services.progress_tracker import progress_tracker as _pt
        _job_id_for_progress = None

        phase_started = time.perf_counter()
        selected_scope_insufficient_reason, selected_scope_ctx = self._selected_source_insufficiency(
            question=question,
            asset=asset,
            source_ids=source_ids,
        )
        phase_timings["selected_scope_check_ms"] = int((time.perf_counter() - phase_started) * 1000)
        if selected_scope_insufficient_reason:
            return self._complete_insufficient_selected_scope(
                question=question,
                asset=asset,
                num_rounds=num_rounds,
                source_ids=source_ids,
                reason=selected_scope_insufficient_reason,
                selected_scope_ctx=selected_scope_ctx,
                now=now,
                start_ts=start_ts,
                phase_timings=phase_timings,
                route_identity=route_identity,
            )

        # 1. 聚合上下文（S1：传入 question 供混合召回使用）
        phase_started = time.perf_counter()
        context = self._gather_context(asset, question, source_ids=source_ids)
        self.db.rollback()  # clear any failed tx from _gather_context
        phase_timings["base_context_ms"] = int((time.perf_counter() - phase_started) * 1000)

        # 1.4 V2: 先根据基础上下文确定实际席位。
        phase_started = time.perf_counter()
        orchestrator = CommitteeOrchestrator()
        claims_n = len(context["claims"])
        events_n = len(context["events"])
        has_market = bool(context["market"])
        coverage = min(1.0, (min(claims_n, 10) / 10 + min(events_n, 5) / 5 + (1.0 if has_market else 0)) / 3)
        orch_plan = orchestrator.plan(
            task_mode=task_mode,
            asset=asset,
            data_coverage_score=coverage,
            is_watchlist_asset=True,
            user_force_full=bool(force_seats),
        )
        active_seat_ids = orch_plan.activated_seats
        token_budget = orch_plan.total_estimated_tokens
        logger.info(
            f"[Committee] Orchestrator: mode={task_mode}, seats={active_seat_ids}, "
            f"budget={token_budget}"
        )
        phase_timings["orchestration_ms"] = int((time.perf_counter() - phase_started) * 1000)

        # 1.5 只为实际激活席位补充角色专属证据，避免查询随后会被忽略的席位。
        phase_started = time.perf_counter()
        agent_contexts = self._gather_agent_contexts(
            question=question,
            asset=asset,
            context=context,
            active_seat_ids=active_seat_ids,
            source_ids=source_ids,
        )
        phase_timings["role_retrieval_ms"] = int((time.perf_counter() - phase_started) * 1000)

        # 2. 创建 DebateRecord（A6/V2：优先 model_by_seat JSON；旧三列 dual-write 兼容）
        from app.utils.debate_seats import build_seat_models
        configured_seat_models = {
            role: route["primary_model"]
            for role, route in route_identity["configuration"]["routes"].items()
            if role in ROLE_SKILL_MAP
        }
        active_configured_models = {
            role: configured_seat_models[role]
            for role in active_seat_ids
            if role in configured_seat_models
        }
        _baseline = configured_seat_models["baseline"]
        _probability = configured_seat_models["probability"]
        _risk = configured_seat_models["risk"]
        debate = DebateRecord(
            id=_audit_debate_id or str(ULID()),
            question=question,
            asset=asset,
            committee_level="triple",
            model_by_seat=build_seat_models(**active_configured_models),
            # Legacy V1 dual-write（向后兼容；待 0010 migration 移除）
            model_bull=_baseline,
            model_bear=_probability,
            model_neutral=_risk,
            status="running",
            context_claims=context["claims"],
            context_events=context["events"],
            context_market=context["market"],
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        self.db.add(debate)
        self.db.flush()

        # 记录 job_run
        job = JobRun(
            id=_audit_job_id or str(ULID()),
            job_type="committee_debate",
            status="running",
            triggered_by="api",
            input_params={"question": question, "asset": asset, "num_rounds": num_rounds, "source_ids": source_ids or []},
            result_summary={},
            source_ids=source_ids or [],
            object_scope={
                "asset": asset,
                "question": question,
                "source_ids": source_ids or [],
            },
            started_at=now,
            related_object_id=debate.id,
            related_object_type="debate_record",
            retry_count=0,
            created_at=now,
            updated_at=now,
        )
        self.db.add(job)
        self.db.flush()

        # 启动进度追踪
        _job_id_for_progress = job.id
        _pt.start_job(
            job.id,
            "committee_debate",
            source_ids=source_ids or [],
            object_scope={
                "asset": asset,
                "question": question,
                "source_ids": source_ids or [],
            },
            related_object_id=debate.id,
            related_object_type="debate_record",
        )
        _pt.update_stage(job.id, "context", "completed", message="上下文聚合完成")

        _total_cost: float = 0.0
        _llm_traces: list[dict] = []
        seat_states: dict[str, dict[str, Any]] = {
            role: {
                "successful_rounds": 0,
                "error_rounds": [],
                "mock_rounds": [],
            }
            for role in active_seat_ids
        }
        seat_quality: dict[str, Any] | None = None

        try:
            # 3. 多轮辩论
            all_rounds: list[DebateRound] = []
            previous_summaries: list[dict] = []

            def _capture_llm_scope(step: str, route_key: str, scope) -> list[dict]:
                route = route_identity["configuration"]["routes"][route_key]
                requested_model = route.get("primary_model")
                route_fallbacks = list(route.get("native_fallback_models") or [])
                usage_calls = [dict(call) for call in scope.calls]
                retry_count = max(0, len(usage_calls) - 1)
                stage_traces: list[dict] = []
                for schema_attempt, usage in enumerate(usage_calls, start=1):
                    actual_model = usage.get("model") or requested_model
                    configured_fallback_models = usage.get("configured_fallback_models")
                    if not isinstance(configured_fallback_models, list):
                        configured_fallback_models = route_fallbacks
                    fallback_chain = usage.get("fallback_chain")
                    if not isinstance(fallback_chain, list):
                        fallback_chain = list(
                            dict.fromkeys(
                                model
                                for model in [requested_model, actual_model]
                                if model
                            )
                        )
                    trace = {
                        "step": step,
                        "schema_attempt": schema_attempt,
                        "schema_retry_count": retry_count,
                        "model": actual_model,
                        "actual_model": actual_model,
                        "requested_model": usage.get("requested_model") or requested_model,
                        "configured_fallback_models": configured_fallback_models,
                        "skill_role": usage.get("skill_role") or ROLE_SKILL_MAP.get(route_key, "committee_postprocess"),
                        "duration_ms": usage.get("duration_ms"),
                        "prompt_hash": usage.get("prompt_hash"),
                        "response_hash": usage.get("response_hash"),
                        "fallback_chain": fallback_chain,
                        "response_chars": usage.get("response_chars"),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "cost_usd": usage.get("cost_usd") or 0.0,
                        "cost_source": usage.get("cost_source"),
                        "generation_id": usage.get("generation_id"),
                        "mock": bool(usage.get("mock")),
                        "error_type": usage.get("error_type"),
                    }
                    stage_traces.append(trace)
                    _llm_traces.append(trace)
                return stage_traces

            _role_labels = ROLE_DISPLAY_NAMES

            for round_num in range(1, num_rounds + 1):
                for role in active_seat_ids:
                    stage_key = f"round_{round_num}_{role}"
                    _pt.update_stage(
                        job.id, stage_key, "running",
                        message=f"第{round_num}轮·{_role_labels.get(role, role)} 分析中...",
                    )

                    round_input = DebateRoundInput(
                        debate_id=debate.id,
                        round_number=round_num,
                        role=role,
                        question=question,
                        asset=asset,
                        context_claims=context["claims"],
                        context_events=context["events"],
                        context_market=context["market"],
                        previous_rounds=previous_summaries,
                        # G18: 本角色专属额外证据（只发给该 role，其他角色看不到）
                        agent_specific_claims=agent_contexts.get(role, []),
                        model_override=active_configured_models.get(role),
                    )
                    t0 = time.time()
                    with cost_scope(tag=stage_key) as round_scope:
                        try:
                            output = self.debate_skill.debate_round(round_input)
                        finally:
                            stage_traces = _capture_llm_scope(stage_key, role, round_scope)
                            _total_cost += sum(
                                float(call.get("cost_usd") or 0.0)
                                for call in stage_traces
                            )
                    latency = int((time.time() - t0) * 1000)
                    _last_usage = stage_traces[-1] if stage_traces else {}
                    round_is_mock = bool(output.is_mock) or any(
                        call.get("mock") is True for call in stage_traces
                    )
                    if output.error:
                        seat_states[role]["error_rounds"].append(round_num)
                    if round_is_mock:
                        seat_states[role]["mock_rounds"].append(round_num)
                    if not output.error and not round_is_mock:
                        seat_states[role]["successful_rounds"] += 1

                    model_used = str(
                        _last_usage.get("model")
                        or _last_usage.get("requested_model")
                        or active_configured_models.get(role)
                        or _baseline
                    )
                    actual_models = dict(debate.model_by_seat or {})
                    actual_models[role] = model_used
                    debate.model_by_seat = actual_models
                    if role == "baseline":
                        debate.model_bull = model_used
                    elif role == "probability":
                        debate.model_bear = model_used
                    elif role == "risk":
                        debate.model_neutral = model_used

                    dr = DebateRound(
                        id=str(ULID()),
                        debate_id=debate.id,
                        round_number=round_num,
                        role=role,
                        model_used=model_used,
                        position=output.position,
                        key_arguments=output.key_arguments,
                        challenges=output.challenges,
                        concessions=output.concessions,
                        confidence=output.confidence,
                        direction=output.direction,
                        latency_ms=latency,
                        is_mock=round_is_mock,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    )
                    self.db.add(dr)
                    self.db.flush()
                    all_rounds.append(dr)
                    previous_summaries.append({
                        "round": round_num, "role": role,
                        "position": output.position,
                    })

                    _pt.update_stage(
                        job.id, stage_key, "completed",
                        detail=f"方向={output.direction}, 信心={output.confidence:.2f}",
                    )

            seat_quality = self._build_seat_quality(
                active_seat_ids=active_seat_ids,
                seat_states=seat_states,
                expected_rounds=num_rounds,
            )
            seat_quality_warnings = self._seat_quality_warnings(seat_quality)

            # 4. 最终裁决
            _pt.update_stage(job.id, "adjudication", "running", message="裁决官综合分析中...")
            adj_input = AdjudicationInput(
                debate_id=debate.id,
                question=question,
                asset=asset,
                rounds=[
                    {
                        "role": r.role,
                        "round_number": r.round_number,
                        "position": r.position,
                        "key_arguments": r.key_arguments,
                        "challenges": r.challenges,
                        "concessions": r.concessions,
                        "confidence": r.confidence,
                        "direction": r.direction,
                    }
                    for r in all_rounds
                ],
                activated_seats=active_seat_ids,
            )
            with cost_scope(tag="adjudication") as adjudication_scope:
                try:
                    adj_output = self.adjudication_skill.adjudicate(adj_input)
                finally:
                    adjudication_traces = _capture_llm_scope(
                        "adjudication",
                        "adjudication",
                        adjudication_scope,
                    )
                    _total_cost += sum(
                        float(call.get("cost_usd") or 0.0)
                        for call in adjudication_traces
                    )
            _last_usage = adjudication_traces[-1] if adjudication_traces else {}
            adjudicator_model_used = str(
                _last_usage.get("model")
                or _last_usage.get("requested_model")
                or route_identity["configuration"]["routes"]["adjudication"]["primary_model"]
            )
            adjudication_is_mock = bool(adj_output.is_mock) or any(
                call.get("mock") is True for call in adjudication_traces
            )
            committee_is_mock = adjudication_is_mock or bool(
                seat_quality.get("mock_seat_count")
            )
            adj_output.is_mock = committee_is_mock
            adj_output.synthesis, material_anchor_count = _ensure_material_claim_anchors(
                adj_output.synthesis,
                context["claims"],
            )
            adjudication_recovery_warnings: list[dict[str, str]] = []
            if material_anchor_count:
                adjudication_recovery_warnings.append(
                    {
                        "code": "committee_material_evidence_anchor_added",
                        "message": (
                            f"最终裁决遗漏了 {material_anchor_count} 条关键证据细节，"
                            "系统已从选定 Claim 补回证据锚点。"
                        ),
                        "severity": "warning",
                    }
                )
            adjudication_quality_warnings: list[dict[str, str]] = []
            if adj_output.error:
                adjudication_quality_warnings.append(
                    {
                        "code": "committee_adjudication_error",
                        "message": "Final adjudication required a deterministic fallback.",
                        "severity": "error",
                    }
                )
            if adjudication_is_mock:
                adjudication_quality_warnings.append(
                    {
                        "code": "committee_adjudication_mock",
                        "message": "Final adjudication used mock or simulated model output.",
                        "severity": "error",
                    }
                )
            evidence_count = len(context["claims"]) + len(context["events"]) + (1 if context["market"] else 0)
            committee_scope = context.get("scope", {}) or {}
            committee_quality_filters = {
                "evidence_sufficiency": "claim_supported" if evidence_count else "insufficient",
            }
            committee_field_names = list(_COMMITTEE_FIELD_NAMES)
            committee_field_values = {
                "direction": adj_output.direction,
                "recommended_action": adj_output.recommended_action,
                "synthesis": adj_output.synthesis,
                "risk_warnings": adj_output.risk_warnings,
                "structure_warnings": adj_output.structure_warnings,
            }
            preliminary_field_evidence = build_field_evidence_contract(
                field_names=committee_field_names,
                decision_ready=True,
                quality_filters=committee_quality_filters,
                source_ids=committee_scope.get("source_ids", []),
                claim_ids=[
                    str(claim.get("id"))
                    for claim in context["claims"]
                    if isinstance(claim, dict) and claim.get("id")
                ],
                event_ids=[
                    event.get("id", "")
                    for event in context["events"]
                    if isinstance(event, dict) and event.get("id")
                ],
                field_values=committee_field_values,
                claim_catalog=context["claims"],
                basis_fields=["direction", "synthesis"],
                model_fields=["risk_warnings", "structure_warnings"],
                policy_fields=["recommended_action"],
            )
            attribution_warnings = field_evidence_warnings(preliminary_field_evidence)
            guardrail_warnings = [
                *(adj_output.structure_warnings or []),
                *(adj_output.evidence_quality_warnings or []),
                *seat_quality_warnings,
                *adjudication_quality_warnings,
                *attribution_warnings,
            ]
            decision_guardrail = build_structured_decision_guardrail(
                mode="committee",
                recommended_action=adj_output.recommended_action,
                evidence_count=evidence_count,
                source_mode="live" if evidence_count else "mock",
                warnings=guardrail_warnings,
                is_mock=committee_is_mock,
                confidence=adj_output.conviction,
            )
            adj_output.recommended_action = decision_guardrail["effective_action"]
            action_economics = dict(adj_output.action_economics or {})
            action_economics["decision_guardrail"] = decision_guardrail
            if decision_guardrail["status"] == "downgraded":
                guardrail_warning = (
                    "决策护栏已将最终操作建议降级为 watch；"
                    f"原建议为 {decision_guardrail['original_action']}，原因："
                    f"{', '.join(decision_guardrail['reasons']) or 'quality_guardrail'}"
                )
                adj_output.evidence_quality_warnings = [
                    *(adj_output.evidence_quality_warnings or []),
                    guardrail_warning,
                ]
            stored_action_economics = store_adjudication_metadata(
                action_economics,
                probability_score=adj_output.probability_score,
                audit_score=adj_output.audit_score,
                evidence_quality_warnings=adj_output.evidence_quality_warnings,
            )
            adj_output.action_economics = stored_action_economics
            _pt.update_stage(job.id, "adjudication", "completed", message="裁决完成")
            _pt.update_stage(job.id, "persist", "running", message="保存结果中...")

            adj = FinalAdjudication(
                id=str(ULID()),
                debate_id=debate.id,
                direction=adj_output.direction,
                conviction=adj_output.conviction,
                recommended_action=adj_output.recommended_action,
                consensus_level=adj_output.consensus_level,
                synthesis=adj_output.synthesis,
                bull_score=adj_output.baseline_score,     # DB 列兼容
                bear_score=adj_output.risk_score,          # DB 列兼容
                key_agreements=adj_output.key_agreements,
                key_disagreements=adj_output.key_disagreements,
                structure_score=adj_output.structure_score,
                structure_warnings=adj_output.structure_warnings,
                risk_warnings=adj_output.risk_warnings,
                hedging_advice=adj_output.hedging_advice,
                action_economics=stored_action_economics,
                minority_view=adj_output.minority_view,
                invalidation_conditions=adj_output.invalidation_conditions,
                adjudicated_by=adjudicator_model_used,
                is_mock=adj_output.is_mock,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            self.db.add(adj)

            # 5. 保存 DecisionSnapshot（V2：冻结决策时点证据）
            try:
                snapshot = DecisionSnapshot(
                    id=str(ULID()),
                    trigger_type="debate",
                    trigger_id=debate.id,
                    asset=asset,
                    question=question,
                    snapshot_at=datetime.now(timezone.utc),
                    claims_snapshot=context["claims"],
                    claims_count=len(context["claims"]),
                    data_coverage={
                        "claims": len(context["claims"]),
                        "events": len(context["events"]),
                        "has_market": bool(context["market"]),
                    },
                    market_snapshot=context["market"],
                    flow_snapshot={},
                    events_snapshot=context["events"],
                    macro_snapshot={},
                    signal_scorecard={},
                    activated_seats=active_seat_ids,
                    evidence_budget={"total": token_budget},
                    final_direction=adj_output.direction,
                    final_conviction=adj_output.conviction,
                    final_action=adj_output.recommended_action,
                    synthesis_summary=adj_output.synthesis[:500] if adj_output.synthesis else None,
                    replay_status="pending",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                self.db.add(snapshot)
                self.db.flush()
            except Exception as snap_err:
                logger.warning(f"[Committee] DecisionSnapshot save failed (non-fatal): {snap_err}")

            # 6. 更新 DebateRecord
            completed_at = datetime.now(timezone.utc)
            debate.status = "completed"
            debate.final_direction = adj_output.direction
            debate.final_conviction = adj_output.conviction
            debate.final_action = adj_output.recommended_action
            debate.consensus_level = adj_output.consensus_level
            debate.completed_at = completed_at
            debate.duration_ms = int((time.time() - start_ts) * 1000)
            debate.updated_at = completed_at

            job.status = "completed"
            job.completed_at = completed_at
            job.duration_ms = debate.duration_ms
            job.retry_count = sum(
                1 for call in _llm_traces if int(call.get("schema_attempt") or 1) > 1
            )
            llm_trace = public_llm_trace({
                "call_count": len(_llm_traces),
                "total_cost_usd": round(_total_cost, 6),
                "calls": _llm_traces,
            })
            timing_trace = self._build_timing_trace(
                total_ms=debate.duration_ms,
                llm_calls=_llm_traces,
                phase_timings=phase_timings,
            )
            context_scope = context.get("scope", {}) or {}
            source_claim_ids = [
                str(claim.get("id"))
                for claim in context["claims"]
                if isinstance(claim, dict) and claim.get("id")
            ]
            quality_filters = {
                "evidence_sufficiency": "claim_supported" if evidence_count else "insufficient",
                "claims_used": len(context["claims"]),
                "events_used": len(context["events"]),
                "source_mode": context_scope.get("mode", "global"),
                "committee_quorum": "met" if seat_quality.get("quorum_met") else "not_met",
                "successful_seats": seat_quality.get("successful_seat_count", 0),
                "minimum_successful_seats": seat_quality.get("minimum_successful_seats", 0),
            }
            quality_warnings: list[Any] = [
                *seat_quality_warnings,
                *adjudication_quality_warnings,
                *adjudication_recovery_warnings,
            ]
            quality_warnings.extend(
                {
                    "code": "committee_output_warning",
                    "message": str(warning),
                    "severity": "warning",
                }
                for warning in [
                    *(adj_output.structure_warnings or []),
                    *(adj_output.evidence_quality_warnings or []),
                ]
                if str(warning).strip()
            )
            quality_report = build_structured_quality_report(
                decision_ready=decision_guardrail.get("decision_ready") is True,
                quality_filters=quality_filters,
                claims_used=len(context["claims"]),
                events_used=len(context["events"]),
                has_market=bool(context["market"]),
                warnings=quality_warnings,
                duration_ms=debate.duration_ms,
                source_mode=context_scope.get("mode", "global"),
                setup_quality=adj_output.consensus_level,
                field_names=committee_field_names,
                source_ids=context_scope.get("source_ids", []),
                claim_ids=source_claim_ids,
                event_ids=[
                    event.get("id", "")
                    for event in context["events"]
                    if isinstance(event, dict) and event.get("id")
                ],
                field_values=committee_field_values,
                claim_catalog=context["claims"],
                basis_fields=["direction", "synthesis"],
                model_fields=["risk_warnings", "structure_warnings"],
                policy_fields=["recommended_action"],
                extra_evidence={
                    "activated_seats": active_seat_ids,
                    "round_count": len(all_rounds),
                    "consensus_level": adj_output.consensus_level,
                    "seat_quality": seat_quality,
                },
            )
            job.result_summary = {
                "debate_id": debate.id,
                "direction": adj_output.direction,
                "cost_usd": _total_cost,
                "claims_used": len(context["claims"]),
                "events_used": len(context["events"]),
                "source_claim_ids": source_claim_ids,
                "decision_guardrail": decision_guardrail,
                "llm_trace": llm_trace,
                "quality_report": quality_report,
                "scope": context_scope,
                "quality_filters": quality_filters,
                "seat_quality": seat_quality,
                "timing_trace": timing_trace,
                "route_identity": route_identity,
            }
            job.updated_at = completed_at
            from app.services.decision_trace_service import record_decision_trace_bundle, record_trace_event
            stance_distribution: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0, "conditional": 0}
            retrieval_paths: dict[str, int] = {}
            for claim in context["claims"]:
                stance = str(claim.get("stance") or "neutral")
                if stance not in stance_distribution:
                    stance = "conditional"
                stance_distribution[stance] += 1
                for path in claim.get("_retrieval_paths") or []:
                    retrieval_paths[str(path)] = retrieval_paths.get(str(path), 0) + 1
            record_trace_event(
                self.db,
                related_object_type="debate",
                related_object_id=debate.id,
                event_type="retrieval",
                step_name="committee_context",
                status="ok" if evidence_count else "warning",
                job_id=job.id,
                source_ids=context_scope.get("source_ids", []),
                payload={
                    "scope_mode": context_scope.get("mode", "global"),
                    "scope_resolved_from": "request_source_ids" if context_scope.get("source_ids") else "asset",
                    "search_tier": "committee_mixed",
                    "claims_count": len(context["claims"]),
                    "events_count": len(context["events"]),
                    "has_market": bool(context["market"]),
                    "context_items": evidence_count,
                    "stance_distribution": stance_distribution,
                    "retrieval_paths": retrieval_paths,
                    "agent_retrieval_counts": {role: len(items) for role, items in agent_contexts.items()},
                    "source_scope": context_scope,
                    "quality_filters": quality_filters,
                },
            )
            record_decision_trace_bundle(
                self.db,
                related_object_type="debate",
                related_object_id=debate.id,
                job_id=job.id,
                source_ids=context.get("scope", {}).get("source_ids", []),
                scope=context.get("scope", {}),
                quality_report=quality_report,
                decision_guardrail=decision_guardrail,
                llm_trace=llm_trace,
            )

            safe_commit(self.db)
            _pt.complete_job(job.id, message=f"委员会分析完成，方向={adj_output.direction}")

            data_sources = {
                "claims": {
                    "is_mock": False,
                    "count": len(context["claims"]),
                    "items": [
                        {
                            "summary": c.get("summary", "")[:160],
                            "stance": c.get("stance", "neutral"),
                            "confidence": c.get("confidence", 0),
                        }
                        for c in context["claims"][:5]
                    ],
                },
                "events": {
                    "is_mock": False,
                    "count": len(context["events"]),
                    "items": [
                        {
                            "title": e.get("title", e.get("summary", ""))[:160],
                            "source": e.get("source", ""),
                        }
                        for e in context["events"][:5]
                    ],
                },
                "market": {
                    "is_mock": False,
                    "asset": asset,
                    "snapshot": {
                        k: v for k, v in (context["market"] or {}).items()
                        if k in ("price", "rsi_14", "change_24h", "volume_24h", "ma_20", "ma_50")
                    } if context["market"] else {},
                },
                "flow": {
                    "is_mock": False,
                    "signals_count": 0,
                    "snapshot": {},
                },
                "agent_retrieval": {
                    "counts": {role: len(items) for role, items in agent_contexts.items()},
                },
            }

            return {
                "success": True,
                "debate_id": debate.id,
                "direction": adj_output.direction,
                "conviction": adj_output.conviction,
                "recommended_action": adj_output.recommended_action,
                "consensus_level": adj_output.consensus_level,
                "synthesis": adj_output.synthesis,
                "probability_score": adj_output.probability_score,
                "audit_score": adj_output.audit_score,
                "risk_warnings": adj_output.risk_warnings,
                "structure_warnings": adj_output.structure_warnings,
                "evidence_quality_warnings": adj_output.evidence_quality_warnings,
                "action_economics": action_economics,
                "hedging_advice": adj_output.hedging_advice,
                "decision_guardrail": decision_guardrail,
                "quality_report": quality_report,
                "seat_quality": seat_quality,
                "activated_seats": active_seat_ids,
                "round_count": len(all_rounds),
                "is_mock": adj_output.is_mock,
                "cost_usd": round(_total_cost, 6),
                "llm_trace": llm_trace,
                "scope": context.get("scope", {}),
                "quality_filters": quality_filters,
                "data_sources": data_sources,
                "duration_ms": debate.duration_ms,
                "timing_trace": timing_trace,
                "route_identity": route_identity,
            }

        except Exception as e:
            if _job_id_for_progress:
                _pt.fail_job(_job_id_for_progress, COMMITTEE_FAILURE_MESSAGE)
            if seat_quality is None:
                seat_quality = self._build_seat_quality(
                    active_seat_ids=active_seat_ids,
                    seat_states=seat_states,
                    expected_rounds=num_rounds,
                )
            duration_ms = int((time.time() - start_ts) * 1000)
            model_by_seat = dict(debate.model_by_seat or active_configured_models)
            self._persist_failed_run(
                debate_id=debate.id,
                job_id=job.id,
                question=question,
                asset=asset,
                num_rounds=num_rounds,
                source_ids=source_ids,
                started_at=now,
                duration_ms=duration_ms,
                model_by_seat=model_by_seat,
                context=context,
                route_identity=route_identity,
                llm_traces=_llm_traces,
                total_cost=_total_cost,
                seat_quality=seat_quality,
                error=e,
            )
            logger.error(f"[Committee] workflow failed: {type(e).__name__}")
            return {
                "success": False,
                "error": COMMITTEE_FAILURE_MESSAGE,
                "error_code": COMMITTEE_FAILURE_CODE,
                "debate_id": debate.id,
                "job_id": job.id,
            }

    def _gather_agent_contexts(
        self,
        *,
        question: str,
        asset: str,
        context: dict,
        active_seat_ids: list[str],
        source_ids: list[str],
    ) -> dict[str, list[dict]]:
        if not question.strip() or not active_seat_ids:
            return {}

        if source_ids:
            base_claim_ids = {
                str(claim.get("id"))
                for claim in context.get("claims", [])
                if isinstance(claim, dict) and claim.get("id")
            }
            try:
                coverage = ResearchMemoryMCP(self.db).get_data_coverage_summary(source_ids=source_ids)
                total_active_claims = coverage.get("total_active_claims") if isinstance(coverage, dict) else None
            except Exception:
                total_active_claims = None

            if (
                isinstance(total_active_claims, int)
                and not isinstance(total_active_claims, bool)
                and total_active_claims > 0
                and total_active_claims <= len(base_claim_ids)
            ):
                logger.info(
                    "[Committee] role retrieval skipped: selected-source claims are fully covered "
                    f"({len(base_claim_ids)}/{total_active_claims})"
                )
                return {}

        try:
            from app.services.agent_retrieval import gather_for_all_roles

            agent_contexts = gather_for_all_roles(
                self.db,
                active_seats=active_seat_ids,
                base_question=question,
                asset=asset,
                limit_per_role=4,
                base_claims=context.get("claims", []),
                source_ids=source_ids,
            )
            if agent_contexts:
                logger.info(
                    f"[Committee] role retrieval: "
                    f"{ {role: len(items) for role, items in agent_contexts.items()} }"
                )
            return agent_contexts
        except Exception as error:
            logger.warning(f"[Committee] agent retrieval failed (non-fatal): {error}")
            return {}

    def _gather_context(self, asset: str, question: str = "", source_ids: Optional[list[str]] = None) -> dict:
        """聚合市场上下文

        S1 升级：用 query_for_briefing（语义 + 资产 + 高信任 三路混合召回）替换
        原来的 get_active_claims_by_asset（仅按资产时间倒序）。

        收益：语义匹配 + trust 加权 + is_contradicted 降权，让辛论席位
        拿到的证据与 chat/briefing 一致。
        """
        source_ids = clean_source_ids(source_ids)
        memory = ResearchMemoryMCP(self.db)

        try:
            # 优先用 query_for_briefing（三路混合）；若 question 为空则降级
            if question.strip():
                query_kwargs = {"query": question, "asset": asset, "limit": 10}
                if source_ids:
                    query_kwargs["source_ids"] = source_ids
                claims_resp = memory.query_for_briefing(**query_kwargs)
                raw = claims_resp.data or []
                # query_for_briefing 已返回 dict 列表，字段规范对齐下游使用
                claims_data = [
                    {
                        "id": c.get("id", ""),
                        "source_ids": c.get("source_ids", []),
                        "summary": c.get("summary", ""),
                        "full_quote": c.get("full_quote"),
                        "stance": c.get("stance", "neutral"),
                        "confidence": c.get("confidence", 0.5),
                        "trust_weight": c.get("trust_weight", 0.5),
                        "time_horizon": c.get("time_horizon"),
                        "is_contradicted": c.get("is_contradicted", False),
                        "_retrieval_paths": c.get("_retrieval_paths", []),
                    }
                    for c in raw if isinstance(c, dict)
                ]
            else:
                # 降级：无 question 时回退旧逻辑（向后兼容）
                claims_resp = memory.get_active_claims_by_asset(asset, limit=8)
                claims_data = [
                    {
                        "summary": c.summary, "stance": c.stance,
                        "confidence": c.confidence,
                        "trust_weight": getattr(c, 'trust_weight', 0.5),
                    }
                    for c in (claims_resp.data or [])
                ]
        except Exception as _e:
            from loguru import logger
            logger.warning(f"[Committee] query_for_briefing failed, fallback empty: {_e}")
            claims_data = []

        if source_ids:
            # Event and market MCPs are not source-linked today. Pulling their
            # global data would violate an explicit selected-source boundary.
            events_data = []
            market_data = {}
        else:
            event_mcp = EventFeedMCP(self.db)
            market_mcp = MarketDataMCP(self.db)

            try:
                events_resp = event_mcp.fetch_recent_events(asset=asset, limit=4)
                events_data = events_resp.data or []
            except Exception:
                events_data = []

            try:
                market_resp = market_mcp.get_market_state(asset=asset)
                raw_market = market_resp.data or {}
                # 过滤掉 klines_raw 等大字段，避免 JSON 列超限导致事务中断
                if isinstance(raw_market, dict):
                    market_data = {k: v for k, v in raw_market.items() if k != 'klines_raw'}
                else:
                    market_data = {}
            except Exception:
                market_data = {}

        return {
            "claims": claims_data,
            "events": events_data,
            "market": market_data,
            "scope": {
                "mode": "selected_sources" if source_ids else "global",
                "source_ids": source_ids or [],
            },
        }
