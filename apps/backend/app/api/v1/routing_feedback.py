"""B6 · Routing Feedback API — 用户对路由决策的反馈闭环

职责：
    1. 接收用户对某次路由结果的反馈（满意 / 不满意 / 改选了其它模式）
    2. 留痕到 user_overrides 表（target_type="routing"）
    3. 提供聚合统计，用于路由调参页或 settings 展示

为什么复用 user_overrides？
    - 已有表结构完全够用：target_id=message_id, new_value=JSON payload
    - 不新增 migration，降低部署风险
    - override_type="routing_feedback" 与现有 adoption_feedback 模式一致

Payload schema（存入 new_value）:
    {
        "auto_route": "chat",            # 系统自动选择的路由
        "chosen_route": "chat",          # 用户实际使用的路由（点了 👎+切换则为 briefing）
        "is_helpful": true|false|null,   # 👍 / 👎 / 未表态
        "message": "user feedback text", # 可选自由文本
        "intent_mode": "asset_analysis", # 当时的 intent
        "intent_confidence": 0.72,
        "reason": "检测到决策词...",      # 当时的决策理由
        "assets": ["BTC"],
    }
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from ulid import ULID

from app.database import safe_commit
from app.dependencies import get_db
from app.schemas.user_override import UserOverride


router = APIRouter()


# ══════════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════════

_VALID_ROUTES = {"chat", "briefing", "debate"}


class RoutingFeedbackRequest(BaseModel):
    """用户提交对单次路由决策的反馈"""
    message_id: str = Field(..., description="触发路由决策的 ChatMessage.id（作为 target_id）")
    auto_route: str = Field(..., description="系统自动选择的路由: chat | briefing | debate")
    chosen_route: Optional[str] = Field(None, description="用户实际使用的路由；未切换时=auto_route")
    is_helpful: Optional[bool] = Field(None, description="用户 👍 (True) / 👎 (False) / 未表态 (None)")
    message: Optional[str] = Field(None, description="用户自由文本反馈，<=500 字")
    # 诊断快照（由前端从路由决策里透传回来，方便后期统计时不用重查）
    intent_mode: Optional[str] = None
    intent_confidence: Optional[float] = None
    reason: Optional[str] = None
    assets: List[str] = Field(default_factory=list)


class RoutingFeedbackResponse(BaseModel):
    id: str
    message_id: str
    auto_route: str
    chosen_route: str
    is_helpful: Optional[bool]
    created_at: str


class RouteStatBucket(BaseModel):
    """聚合后单条统计"""
    auto_route: str
    total: int
    helpful: int
    unhelpful: int
    no_feedback: int
    switched: int                # 用户切换到其它路由的次数
    helpful_rate: float          # helpful / (helpful + unhelpful)
    accept_rate: float           # 未切换 / total


class RoutingFeedbackStats(BaseModel):
    total_feedback: int
    buckets: List[RouteStatBucket]
    top_switches: List[dict]     # [{"from": "chat", "to": "briefing", "count": 5}, ...]


# ══════════════════════════════════════════════════════════════════
# 提交反馈
# ══════════════════════════════════════════════════════════════════

@router.post("", response_model=RoutingFeedbackResponse)
def submit_routing_feedback(
    req: RoutingFeedbackRequest,
    db: Session = Depends(get_db),
):
    """用户对一次路由决策提交反馈（👍 / 👎 / 切换模式）。

    写入 ``user_overrides``，``target_type="routing"``, ``target_id=message_id``,
    ``override_type="routing_feedback"``。
    """
    # 校验
    if req.auto_route not in _VALID_ROUTES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"auto_route 必须是 {_VALID_ROUTES}")
    chosen_route = req.chosen_route or req.auto_route
    if chosen_route not in _VALID_ROUTES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"chosen_route 必须是 {_VALID_ROUTES}")

    now = datetime.now(timezone.utc)
    payload = {
        "auto_route": req.auto_route,
        "chosen_route": chosen_route,
        "is_helpful": req.is_helpful,
        "message": (req.message or "")[:500],
        "intent_mode": req.intent_mode,
        "intent_confidence": req.intent_confidence,
        "reason": (req.reason or "")[:200],
        "assets": req.assets[:8],
    }

    override = UserOverride(
        id=str(ULID()),
        target_type="routing",
        target_id=req.message_id,
        field_name="routing_feedback",
        old_value=req.auto_route,
        new_value=json.dumps(payload, ensure_ascii=False),
        reason=req.message,
        override_type="routing_feedback",
        extra_data=payload,
        created_at=now,
        updated_at=now,
    )
    db.add(override)
    safe_commit(db)

    logger.info(
        f"[RoutingFeedback] msg={req.message_id} auto={req.auto_route} "
        f"chosen={chosen_route} helpful={req.is_helpful}"
    )

    return RoutingFeedbackResponse(
        id=override.id,
        message_id=req.message_id,
        auto_route=req.auto_route,
        chosen_route=chosen_route,
        is_helpful=req.is_helpful,
        created_at=now.isoformat(),
    )


# ══════════════════════════════════════════════════════════════════
# 统计聚合
# ══════════════════════════════════════════════════════════════════

@router.get("/stats", response_model=RoutingFeedbackStats)
def get_routing_feedback_stats(
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """聚合最近 N 条 routing_feedback，返回各路由的满意率/切换率。

    用于 Settings 页或审计页展示路由质量。
    """
    rows = (
        db.query(UserOverride)
        .filter(UserOverride.override_type == "routing_feedback")
        .order_by(UserOverride.created_at.desc())
        .limit(limit)
        .all()
    )

    per_route: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "helpful": 0, "unhelpful": 0, "no_feedback": 0, "switched": 0}
    )
    switch_counter: Counter = Counter()

    for o in rows:
        data = o.extra_data or {}
        if not isinstance(data, dict):
            continue
        auto = data.get("auto_route") or o.old_value
        if auto not in _VALID_ROUTES:
            continue
        chosen = data.get("chosen_route") or auto
        helpful = data.get("is_helpful")

        bucket = per_route[auto]
        bucket["total"] += 1
        if helpful is True:
            bucket["helpful"] += 1
        elif helpful is False:
            bucket["unhelpful"] += 1
        else:
            bucket["no_feedback"] += 1
        if chosen != auto:
            bucket["switched"] += 1
            switch_counter[(auto, chosen)] += 1

    buckets: List[RouteStatBucket] = []
    for route in sorted(per_route.keys()):
        b = per_route[route]
        h_plus_u = b["helpful"] + b["unhelpful"]
        buckets.append(RouteStatBucket(
            auto_route=route,
            total=b["total"],
            helpful=b["helpful"],
            unhelpful=b["unhelpful"],
            no_feedback=b["no_feedback"],
            switched=b["switched"],
            helpful_rate=round(b["helpful"] / h_plus_u, 3) if h_plus_u else 0.0,
            accept_rate=round(1.0 - b["switched"] / b["total"], 3) if b["total"] else 0.0,
        ))

    top_switches = [
        {"from": k[0], "to": k[1], "count": v}
        for k, v in switch_counter.most_common(10)
    ]

    return RoutingFeedbackStats(
        total_feedback=sum(b.total for b in buckets),
        buckets=buckets,
        top_switches=top_switches,
    )


# ══════════════════════════════════════════════════════════════════
# 单条查询（辅助）
# ══════════════════════════════════════════════════════════════════

@router.get("/by-message/{message_id}")
def get_feedback_for_message(message_id: str, db: Session = Depends(get_db)):
    """查询某条消息是否已有反馈（前端防重复提交用）"""
    rows = (
        db.query(UserOverride)
        .filter(
            UserOverride.override_type == "routing_feedback",
            UserOverride.target_id == message_id,
        )
        .order_by(UserOverride.created_at.desc())
        .all()
    )
    return {
        "message_id": message_id,
        "count": len(rows),
        "feedback": [
            {
                "id": o.id,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "payload": o.extra_data,
            }
            for o in rows
        ],
    }
