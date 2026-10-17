"""Decision Replay API — 决策回放

核心原则：不允许事后诸葛亮（no hindsight bias）。
回放时只展示当时已知的信息，然后对比事后实际结果。

功能：
1. 列出待回放的决策快照
2. 标注后验结果（实际方向、PnL%）
3. 触发 LLM 回放分析（误导证据、遗漏信号）
4. 查看回放详情
"""
import json
import re
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ulid import ULID
from loguru import logger
from app.utils.json_repair import robust_json_parse

from app.database import safe_commit
from app.dependencies import get_db
from app.schemas.chat import ChatMessage
from app.schemas.decision_trace_event import DecisionTraceEvent
from app.schemas.decision_snapshot import DecisionSnapshot
from app.utils.llm_client import llm_client

router = APIRouter()


# ── 请求模型 ────────────────────────────────────────────────────

class OutcomeAnnotation(BaseModel):
    outcome_direction: str                    # bullish | bearish | neutral
    outcome_pnl_pct: Optional[float] = None   # 实际收益百分比
    notes: Optional[str] = None               # 补充说明


class ReplayTriggerRequest(BaseModel):
    max_tokens: int = Field(default=3000, ge=3000, le=6000)


class ChatSnapshotRequest(BaseModel):
    asset: Optional[str] = None
    final_direction: Optional[str] = None
    final_action: Optional[str] = "watch"
    final_conviction: Optional[float] = None


SYSTEM_SNAPSHOT_PATTERNS = (
    re.compile(r"runtime smoke", re.IGNORECASE),
    re.compile(r"main-flow-smoke", re.IGNORECASE),
    re.compile(r"mvp acceptance", re.IGNORECASE),
    re.compile(r"scope acceptance", re.IGNORECASE),
    re.compile(r"scope_acceptance", re.IGNORECASE),
    re.compile(r"codex-pass", re.IGNORECASE),
    re.compile(r"pass\d*\s*smoke", re.IGNORECASE),
    re.compile(r"ui-pass\d*", re.IGNORECASE),
)
WORKFLOW_TRIGGER_TYPES = {"briefing", "debate", "scout"}


def _find_chat_decision_snapshot(db: Session, message_id: str) -> DecisionSnapshot | None:
    return (
        db.query(DecisionSnapshot)
        .filter(
            DecisionSnapshot.trigger_type == "chat",
            DecisionSnapshot.trigger_id == message_id,
        )
        .first()
    )


def _chat_snapshot_response(status: str, snapshot: DecisionSnapshot) -> dict:
    return {
        "status": status,
        "snapshot_id": snapshot.id,
        "trigger_type": snapshot.trigger_type,
        "claims_count": snapshot.claims_count,
    }


def _cjk_count(value: str) -> int:
    return sum(1 for char in value if "\u3400" <= char <= "\u9fff")


def _repair_text_encoding(value: str) -> str:
    if not any(char in value for char in ("Ã", "Â", "â", "å", "æ", "ç", "è", "é", "ä", "ã", "ï")) and not any(
        0x80 <= ord(char) <= 0x9F for char in value
    ):
        return value
    try:
        decoded = value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value
    return decoded if _cjk_count(decoded) > _cjk_count(value) else value


def _display_question(value: Optional[str]) -> str:
    if not value:
        return ""
    repaired = _repair_text_encoding(value)
    if repaired.count("?") >= 5 and _cjk_count(repaired) == 0:
        return "历史记录标题不可读（旧数据）"
    return repaired


def _repair_json_text(value):
    if isinstance(value, str):
        return _repair_text_encoding(value)
    if isinstance(value, list):
        return [_repair_json_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_json_text(item) for key, item in value.items()}
    return value


def _snapshot_data_coverage(snapshot: DecisionSnapshot) -> dict:
    return snapshot.data_coverage if isinstance(snapshot.data_coverage, dict) else {}


def _is_system_test_snapshot(snapshot: DecisionSnapshot) -> bool:
    if snapshot.trigger_type == "chat":
        return False
    data_coverage = _snapshot_data_coverage(snapshot)
    if data_coverage.get("visibility_scope") == "system_test" or data_coverage.get("record_origin") == "system_test":
        return True
    text = " ".join(
        str(part or "")
        for part in (
            snapshot.question,
            snapshot.trigger_id,
            snapshot.asset,
            snapshot.synthesis_summary,
        )
    )
    return any(pattern.search(text) for pattern in SYSTEM_SNAPSHOT_PATTERNS)


def _snapshot_record_origin(snapshot: DecisionSnapshot) -> str:
    data_coverage = _snapshot_data_coverage(snapshot)
    explicit_origin = data_coverage.get("record_origin")
    source = data_coverage.get("source")
    if explicit_origin in {"chat_user_confirmed", "system_test"}:
        return explicit_origin
    if source == "chat_user_confirmed" or snapshot.trigger_type == "chat":
        return "chat_user_confirmed"
    if _is_system_test_snapshot(snapshot):
        return "system_test"
    if snapshot.trigger_type in WORKFLOW_TRIGGER_TYPES:
        return f"{snapshot.trigger_type}_workflow"
    return "unknown"


def _snapshot_visibility_scope(snapshot: DecisionSnapshot) -> str:
    origin = _snapshot_record_origin(snapshot)
    if origin == "system_test":
        return "system_test"
    if origin == "chat_user_confirmed":
        return "user_primary"
    return "workflow_history"


def _snapshot_visibility_metadata(snapshot: DecisionSnapshot) -> dict:
    origin = _snapshot_record_origin(snapshot)
    visibility_scope = _snapshot_visibility_scope(snapshot)
    return {
        "record_origin": origin,
        "visibility_scope": visibility_scope,
        "is_system_test": visibility_scope == "system_test",
    }


def _snapshot_list_item(snapshot: DecisionSnapshot) -> dict:
    return {
        "id": snapshot.id,
        "trigger_type": snapshot.trigger_type,
        "trigger_id": snapshot.trigger_id,
        "asset": snapshot.asset,
        "question": _display_question(snapshot.question)[:100],
        "snapshot_at": snapshot.snapshot_at.isoformat() if snapshot.snapshot_at else None,
        "final_direction": snapshot.final_direction,
        "final_conviction": snapshot.final_conviction,
        "final_action": snapshot.final_action,
        "activated_seats": snapshot.activated_seats,
        "claims_count": snapshot.claims_count,
        "replay_status": snapshot.replay_status,
        "outcome_direction": snapshot.outcome_direction,
        "outcome_pnl_pct": snapshot.outcome_pnl_pct,
        **_snapshot_visibility_metadata(snapshot),
    }


def _assert_chat_message_decision_ready(payload: dict, *, message_id: str, db: Session) -> None:
    """Server-side guard for UI-disabled decision snapshot saves."""
    quality_report = payload.get("quality_report") if isinstance(payload, dict) else None
    if not isinstance(quality_report, dict) or quality_report.get("decision_ready") is not True:
        raise HTTPException(
            status_code=409,
            detail="quality_report.decision_ready must be true before saving a chat message as a DecisionSnapshot",
        )

    decision_guardrail = payload.get("decision_guardrail") if isinstance(payload, dict) else None
    if isinstance(decision_guardrail, dict) and decision_guardrail.get("status") == "blocked":
        raise HTTPException(
            status_code=409,
            detail="decision_guardrail.status is blocked; chat message cannot be saved as a DecisionSnapshot",
        )

    trace_events = (
        db.query(DecisionTraceEvent)
        .filter(
            DecisionTraceEvent.related_object_type == "chat_message",
            DecisionTraceEvent.related_object_id == message_id,
        )
        .all()
    )
    event_types = {event.event_type for event in trace_events}
    if not {"quality", "llm_call"}.issubset(event_types):
        raise HTTPException(
            status_code=409,
            detail="server-generated decision trace is required before saving a chat message as a DecisionSnapshot",
        )

    quality_events = [event for event in trace_events if event.event_type == "quality"]
    if not any(isinstance(event.payload, dict) and event.payload.get("decision_ready") is True for event in quality_events):
        raise HTTPException(
            status_code=409,
            detail="server-generated quality trace must mark decision_ready before saving a DecisionSnapshot",
        )

    llm_events = [event for event in trace_events if event.event_type == "llm_call"]
    has_successful_non_mock_llm = any(
        event.status not in {"mock", "error"}
        and not (isinstance(event.payload, dict) and event.payload.get("mock") is True)
        for event in llm_events
    )
    if not has_successful_non_mock_llm:
        raise HTTPException(
            status_code=409,
            detail="server-generated non-mock LLM trace is required before saving a DecisionSnapshot",
        )


# ── 列出待回放快照 ──────────────────────────────────────────────

@router.get("")
def list_snapshots(
    replay_status: Optional[str] = None,
    asset: Optional[str] = None,
    include_system: bool = True,
    limit: int = 30,
    db: Session = Depends(get_db),
):
    """列出决策快照，支持按回放状态和资产过滤"""
    q = db.query(DecisionSnapshot).order_by(DecisionSnapshot.snapshot_at.desc())
    if replay_status:
        q = q.filter(DecisionSnapshot.replay_status == replay_status)
    if asset:
        q = q.filter(DecisionSnapshot.asset == asset)
    query_limit = limit if include_system else max(limit * 5, 500)
    snapshots = q.limit(query_limit).all()
    if not include_system:
        snapshots = [snapshot for snapshot in snapshots if not _is_system_test_snapshot(snapshot)][:limit]
    return {
        "total": len(snapshots),
        "snapshots": [_snapshot_list_item(s) for s in snapshots],
    }


# ── 从聊天回答保存快照 ────────────────────────────────────────

@router.post("/from-chat/{message_id}")
def create_snapshot_from_chat(
    message_id: str,
    req: ChatSnapshotRequest = ChatSnapshotRequest(),
    db: Session = Depends(get_db),
):
    """把用户明确认可的一条 chat assistant message 保存为 DecisionSnapshot。

    这条路径用于 `/chat` 主流程的闭环：系统不会把所有闲聊自动升级为策略，
    只有用户点击“保存为决策记录”时才冻结当时回答、引用、行情和范围元数据。
    """
    assistant = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if not assistant or assistant.role != "assistant":
        raise HTTPException(status_code=404, detail=f"Assistant message {message_id} 不存在")

    user_msg = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.session_id == assistant.session_id,
            ChatMessage.role == "user",
            ChatMessage.created_at <= assistant.created_at,
        )
        .order_by(ChatMessage.created_at.desc())
        .first()
    )

    payload = assistant.payload if isinstance(assistant.payload, dict) else {}
    _assert_chat_message_decision_ready(payload, message_id=assistant.id, db=db)
    existing_snapshot = _find_chat_decision_snapshot(db, assistant.id)
    if existing_snapshot:
        return _chat_snapshot_response("already_exists", existing_snapshot)
    citations = _repair_json_text(payload.get("citations")) if isinstance(payload.get("citations"), list) else []
    market_snapshot = _repair_json_text(payload.get("market_snapshot")) if isinstance(payload.get("market_snapshot"), dict) else {}
    scope = _repair_json_text(payload.get("scope")) if isinstance(payload.get("scope"), dict) else {}
    intent_assets = payload.get("intent_assets") if isinstance(payload.get("intent_assets"), list) else []
    asset = (
        req.asset
        or (intent_assets[0] if intent_assets else None)
        or payload.get("asset_detected")
        or "GENERAL"
    )

    confidences = [
        float(c.get("confidence", 0))
        for c in citations
        if isinstance(c, dict) and isinstance(c.get("confidence"), (int, float))
    ]
    final_conviction = req.final_conviction
    if final_conviction is None and confidences:
        final_conviction = sum(confidences) / len(confidences)

    snapshot = DecisionSnapshot(
        id=str(ULID()),
        trigger_type="chat",
        trigger_id=assistant.id,
        asset=str(asset),
        question=_repair_text_encoding(user_msg.content) if user_msg else "",
        snapshot_at=datetime.now(timezone.utc),
        claims_snapshot=citations,
        claims_count=len(citations),
        data_coverage={
            "claims": len(citations),
            "has_market": bool(market_snapshot),
            "scope": scope,
            "source": "chat_user_confirmed",
        },
        market_snapshot=market_snapshot,
        flow_snapshot={},
        events_snapshot=[],
        macro_snapshot={},
        signal_scorecard={},
        activated_seats=[],
        evidence_budget={
            "claims_used": payload.get("claims_used"),
            "search_tier": payload.get("search_tier"),
        },
        final_direction=req.final_direction,
        final_conviction=final_conviction,
        final_action=req.final_action,
        synthesis_summary=_repair_text_encoding(assistant.content or "")[:1000],
        replay_status="pending",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(snapshot)
    try:
        safe_commit(db)
    except IntegrityError:
        db.rollback()
        existing_snapshot = _find_chat_decision_snapshot(db, assistant.id)
        if existing_snapshot:
            logger.info(
                "DecisionSnapshot already exists after concurrent save: "
                f"trigger_type=chat trigger_id={assistant.id}"
            )
            return _chat_snapshot_response("already_exists", existing_snapshot)
        raise

    return _chat_snapshot_response("ok", snapshot)


# ── 查看快照详情 ────────────────────────────────────────────────

@router.get("/{snapshot_id}")
def get_snapshot_detail(snapshot_id: str, db: Session = Depends(get_db)):
    """查看完整决策快照"""
    s = db.query(DecisionSnapshot).filter(DecisionSnapshot.id == snapshot_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} 不存在")
    return {
        "id": s.id,
        "trigger_type": s.trigger_type,
        "trigger_id": s.trigger_id,
        "asset": s.asset,
        "question": _display_question(s.question),
        "snapshot_at": s.snapshot_at.isoformat() if s.snapshot_at else None,
        "claims_snapshot": _repair_json_text(s.claims_snapshot),
        "claims_count": s.claims_count,
        "data_coverage": _repair_json_text(s.data_coverage),
        "market_snapshot": _repair_json_text(s.market_snapshot),
        "flow_snapshot": _repair_json_text(s.flow_snapshot),
        "events_snapshot": _repair_json_text(s.events_snapshot),
        "macro_snapshot": _repair_json_text(s.macro_snapshot),
        "signal_scorecard": _repair_json_text(s.signal_scorecard),
        "activated_seats": s.activated_seats,
        "evidence_budget": s.evidence_budget,
        "final_direction": s.final_direction,
        "final_conviction": s.final_conviction,
        "final_action": s.final_action,
        "synthesis_summary": _repair_text_encoding(s.synthesis_summary or ""),
        "outcome_direction": s.outcome_direction,
        "outcome_observed_at": s.outcome_observed_at.isoformat() if s.outcome_observed_at else None,
        "outcome_pnl_pct": s.outcome_pnl_pct,
        "replay_status": s.replay_status,
        "replay_notes": s.replay_notes,
        "misleading_evidence": s.misleading_evidence,
        "key_missed_signals": s.key_missed_signals,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        **_snapshot_visibility_metadata(s),
    }


# ── 标注后验结果 ────────────────────────────────────────────────

@router.post("/{snapshot_id}/outcome")
def annotate_outcome(
    snapshot_id: str,
    req: OutcomeAnnotation,
    db: Session = Depends(get_db),
):
    """标注决策快照的后验结果"""
    s = db.query(DecisionSnapshot).filter(DecisionSnapshot.id == snapshot_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} 不存在")

    s.outcome_direction = req.outcome_direction
    s.outcome_pnl_pct = req.outcome_pnl_pct
    s.outcome_observed_at = datetime.now(timezone.utc)
    if req.notes:
        s.replay_notes = req.notes
    s.updated_at = datetime.now(timezone.utc)
    safe_commit(db)

    logger.info(
        f"[Replay] Outcome annotated: {snapshot_id} → {req.outcome_direction} "
        f"(pnl={req.outcome_pnl_pct}%)"
    )
    return {"status": "ok", "snapshot_id": snapshot_id, "outcome_direction": req.outcome_direction}


# ── 触发 LLM 回放分析 ──────────────────────────────────────────

REPLAY_SYSTEM = """你是一位决策回放分析师。
你的任务是对比【决策时点的已知信息】和【事后实际结果】，找出：

1. **误导性证据**：当时哪些信息看起来很重要但实际误导了判断？为什么？
2. **遗漏的关键信号**：当时已有但被忽略的信息中，哪些事后看来是关键的？
3. **决策质量评估**：基于当时的信息，这个决策是否合理？（过程正确 vs 结果正确 是不同的）
4. **可改进点**：下次遇到类似情况，应该注意什么？

⚠ 严格禁止事后诸葛亮（hindsight bias）：
- 不要说"当时就应该知道会跌"之类的话
- 只基于当时可获得的信息评估决策质量
- 区分"过程错误导致的亏损"和"合理决策但运气不好"

输出纯 JSON：
{
  "decision_quality": "good|acceptable|poor",
  "quality_reasoning": "对决策过程质量的评估（100字以内）",
  "misleading_evidence": [{"type": "event|claim|market", "description": "具体误导原因"}],
  "key_missed_signals": ["当时可见但被忽略的关键信号"],
  "improvement_notes": ["可改进建议"],
  "was_process_correct": true,
  "was_outcome_aligned": true
}"""

REPLAY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "decision_replay_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision_quality": {
                    "type": "string",
                    "enum": ["good", "acceptable", "poor"],
                },
                "quality_reasoning": {"type": "string"},
                "misleading_evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["event", "claim", "market"],
                            },
                            "description": {"type": "string"},
                        },
                        "required": ["type", "description"],
                        "additionalProperties": False,
                    },
                },
                "key_missed_signals": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "improvement_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "was_process_correct": {"type": "boolean"},
                "was_outcome_aligned": {"type": "boolean"},
            },
            "required": [
                "decision_quality",
                "quality_reasoning",
                "misleading_evidence",
                "key_missed_signals",
                "improvement_notes",
                "was_process_correct",
                "was_outcome_aligned",
            ],
            "additionalProperties": False,
        },
    },
}

_REPLAY_REQUIRED_FIELDS = tuple(
    REPLAY_RESPONSE_FORMAT["json_schema"]["schema"]["required"]
)


@router.post("/{snapshot_id}/replay")
def trigger_replay_analysis(
    snapshot_id: str,
    req: ReplayTriggerRequest = ReplayTriggerRequest(),
    db: Session = Depends(get_db),
):
    """触发 LLM 回放分析"""
    s = db.query(DecisionSnapshot).filter(DecisionSnapshot.id == snapshot_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} 不存在")

    if not s.outcome_direction:
        raise HTTPException(status_code=400, detail="请先标注后验结果再触发回放分析")

    # 构建回放 prompt
    user_prompt = _build_replay_prompt(s)

    try:
        response = llm_client.complete_with_role_str(
            skill_role="committee_b",
            system_prompt=REPLAY_SYSTEM,
            user_prompt=user_prompt,
            max_tokens=req.max_tokens,
            temperature=0.2,
            response_format=REPLAY_RESPONSE_FORMAT,
        )
        result = _parse_replay_result(response)

        if result["analysis_status"] != "complete":
            s.replay_status = "failed"
            s.replay_notes = result["quality_reasoning"]
            s.updated_at = datetime.now(timezone.utc)
            safe_commit(db)
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "回放模型未返回完整的结构化结果",
                    "missing_fields": result["missing_fields"],
                },
            )

        # 保存回放结果
        s.replay_status = "replayed"
        s.replay_notes = result.get("quality_reasoning", "")
        s.misleading_evidence = result.get("misleading_evidence", [])
        s.key_missed_signals = result.get("key_missed_signals", [])
        s.updated_at = datetime.now(timezone.utc)
        safe_commit(db)

        logger.info(
            f"[Replay] Analysis complete: {snapshot_id} → quality={result.get('decision_quality')}"
        )
        return {
            "status": "ok",
            "snapshot_id": snapshot_id,
            "replay_result": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Replay] Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"回放分析失败: {e}")


def _build_replay_prompt(s: DecisionSnapshot) -> str:
    """构建回放分析 prompt"""
    parts = [
        f"资产：{s.asset}",
        f"问题：{s.question}",
        f"决策时间：{s.snapshot_at.isoformat() if s.snapshot_at else '未知'}",
        "",
        "【决策时点的已知信息】",
        f"私域观点数量：{s.claims_count}",
    ]

    if s.claims_snapshot:
        parts.append("私域观点摘要：")
        for c in s.claims_snapshot[:5]:
            parts.append(f"  [{c.get('stance','')}|信任{c.get('trust_weight',0.5):.1f}] {c.get('summary','')[:80]}")

    if s.market_snapshot:
        ms = s.market_snapshot
        parts.append(f"市场状态：价格 {ms.get('price')}，趋势 {ms.get('trend_direction')}，RSI {ms.get('rsi_14')}")

    if s.events_snapshot:
        parts.append(f"事件（{len(s.events_snapshot)} 条）：")
        for e in s.events_snapshot[:3]:
            parts.append(f"  [{e.get('truth_status','?')}|{e.get('impact_direction','')}] {e.get('title','')[:60]}")

    parts.extend([
        "",
        "【系统判断】",
        f"方向：{s.final_direction}",
        f"确信度：{s.final_conviction}",
        f"建议操作：{s.final_action}",
        f"激活席位：{', '.join(s.activated_seats) if s.activated_seats else '无'}",
        f"综合摘要：{s.synthesis_summary or '无'}",
        "",
        "【事后实际结果】",
        f"实际方向：{s.outcome_direction}",
        f"实际收益：{s.outcome_pnl_pct}%" if s.outcome_pnl_pct is not None else "实际收益：未知",
        f"观察时间：{s.outcome_observed_at.isoformat() if s.outcome_observed_at else '未知'}",
        "",
        "请基于以上信息进行回放分析，输出纯 JSON：",
    ])
    return "\n".join(parts)


def _parse_replay_result(text: str) -> dict:
    """解析回放分析结果"""
    try:
        parsed = robust_json_parse(text, context="ReplayAnalysis")
        if not isinstance(parsed, dict):
            raise ValueError("ReplayAnalysis must be a JSON object")
    except Exception:
        return {
            "decision_quality": "unknown",
            "quality_reasoning": text[:300],
            "misleading_evidence": [],
            "key_missed_signals": [],
            "improvement_notes": [],
            "was_process_correct": None,
            "was_outcome_aligned": None,
            "analysis_status": "invalid",
            "missing_fields": list(_REPLAY_REQUIRED_FIELDS),
        }

    field_validity = {
        "decision_quality": parsed.get("decision_quality") in {"good", "acceptable", "poor"},
        "quality_reasoning": isinstance(parsed.get("quality_reasoning"), str)
        and bool(parsed.get("quality_reasoning", "").strip()),
        "misleading_evidence": isinstance(parsed.get("misleading_evidence"), list),
        "key_missed_signals": isinstance(parsed.get("key_missed_signals"), list),
        "improvement_notes": isinstance(parsed.get("improvement_notes"), list),
        "was_process_correct": isinstance(parsed.get("was_process_correct"), bool),
        "was_outcome_aligned": isinstance(parsed.get("was_outcome_aligned"), bool),
    }
    missing_fields = [
        field for field in _REPLAY_REQUIRED_FIELDS if not field_validity[field]
    ]

    return {
        "decision_quality": (
            parsed.get("decision_quality") if not missing_fields else "unknown"
        ),
        "quality_reasoning": str(parsed.get("quality_reasoning") or "")[:1000],
        "misleading_evidence": (
            parsed.get("misleading_evidence")
            if isinstance(parsed.get("misleading_evidence"), list)
            else []
        ),
        "key_missed_signals": (
            parsed.get("key_missed_signals")
            if isinstance(parsed.get("key_missed_signals"), list)
            else []
        ),
        "improvement_notes": (
            parsed.get("improvement_notes")
            if isinstance(parsed.get("improvement_notes"), list)
            else []
        ),
        "was_process_correct": (
            parsed.get("was_process_correct")
            if isinstance(parsed.get("was_process_correct"), bool)
            else None
        ),
        "was_outcome_aligned": (
            parsed.get("was_outcome_aligned")
            if isinstance(parsed.get("was_outcome_aligned"), bool)
            else None
        ),
        "analysis_status": "complete" if not missing_fields else "incomplete",
        "missing_fields": missing_fields,
    }


# ── 自动 PnL 追踪 ──────────────────────────────────────────────

@router.post("/auto-track-pnl")
def trigger_auto_pnl_tracking(
    dry_run: bool = False,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """G7 · 手动触发 auto PnL 追踪.

    一般由 scheduler 每天 03:00 SH 自动跑。此 endpoint 用于：
    - 开发/调试时立刻补跑
    - 用户希望立即看到最近 snapshot 的 outcome 评估结果

    Args:
        dry_run: True 时只统计不写入字段
        limit: 最多处理多少条（默认 500）

    Returns:
        {status: ok, summary: {checked, evaluated, skipped_*, errors, details}}
    """
    from app.services.auto_pnl_tracker import auto_track_pnl

    summary = auto_track_pnl(db, dry_run=dry_run, limit=limit)
    logger.info(
        f"[Replay] auto-track-pnl manual trigger: "
        f"checked={summary['checked']}, evaluated={summary['evaluated']}, "
        f"dry_run={dry_run}"
    )
    return {"status": "ok", "summary": summary}


# ── 回放统计 ────────────────────────────────────────────────────


@router.get("/stats/summary")
def replay_stats(db: Session = Depends(get_db)):
    """回放统计摘要"""
    all_snapshots = db.query(DecisionSnapshot).all()
    total = len(all_snapshots)
    replayed = sum(1 for s in all_snapshots if s.replay_status == "replayed")
    pending = sum(1 for s in all_snapshots if s.replay_status == "pending")
    with_outcome = sum(1 for s in all_snapshots if s.outcome_direction)
    visibility_scopes = [_snapshot_visibility_scope(s) for s in all_snapshots]
    system_test_snapshots = sum(1 for scope in visibility_scopes if scope == "system_test")
    user_primary_snapshots = sum(1 for scope in visibility_scopes if scope == "user_primary")
    workflow_history_snapshots = sum(1 for scope in visibility_scopes if scope == "workflow_history")

    # 方向准确率
    correct = 0
    incorrect = 0
    for s in all_snapshots:
        if s.final_direction and s.outcome_direction:
            if s.final_direction == s.outcome_direction:
                correct += 1
            elif s.final_direction != "neutral" and s.outcome_direction != "neutral":
                incorrect += 1

    accuracy = correct / (correct + incorrect) if (correct + incorrect) > 0 else None

    # 平均 PnL
    pnls = [s.outcome_pnl_pct for s in all_snapshots if s.outcome_pnl_pct is not None]
    avg_pnl = sum(pnls) / len(pnls) if pnls else None

    return {
        "total_snapshots": total,
        "visible_snapshots": total - system_test_snapshots,
        "user_primary_snapshots": user_primary_snapshots,
        "workflow_history_snapshots": workflow_history_snapshots,
        "system_test_snapshots": system_test_snapshots,
        "replayed": replayed,
        "pending": pending,
        "with_outcome": with_outcome,
        "direction_accuracy": round(accuracy, 3) if accuracy is not None else None,
        "avg_pnl_pct": round(avg_pnl, 2) if avg_pnl is not None else None,
        "total_pnl_observations": len(pnls),
    }
