"""Cost Tracking API — LLM 调用成本查询与预算管理

端点：
- GET  /costs/overview     — 成本概览（Dashboard 用）
- GET  /costs/daily        — 每日成本明细
- GET  /costs/monthly      — 每月成本明细
- GET  /costs/session/{id} — 会话成本明细
- GET  /costs/alerts       — 当前预算告警
- PUT  /costs/budget       — 更新预算限制
"""
from fastapi import APIRouter, Query, Body
from typing import Optional
from dataclasses import asdict

from app.services.cost_tracker import cost_tracker

router = APIRouter()


@router.get("/overview", response_model=dict)
def cost_overview():
    """获取成本概览（今日/本月花费、趋势、告警）"""
    return cost_tracker.get_overview()


@router.get("/daily", response_model=dict)
def daily_cost(day: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认今天")):
    """获取某天的成本汇总"""
    summary = cost_tracker.get_daily_summary(day)
    return asdict(summary)


@router.get("/monthly", response_model=dict)
def monthly_cost(month: Optional[str] = Query(None, description="月份 YYYY-MM，默认本月")):
    """获取某月的成本汇总"""
    summary = cost_tracker.get_monthly_summary(month)
    return asdict(summary)


@router.get("/session/{session_id}", response_model=dict)
def session_cost(session_id: str):
    """获取某个会话的成本汇总"""
    summary = cost_tracker.get_session_summary(session_id)
    return asdict(summary)


@router.get("/history", response_model=dict)
def cost_history(
    granularity: str = Query("daily", description="粒度: daily / monthly / yearly"),
    limit: int = Query(90, description="最近 N 条记录"),
):
    """获取历史成本明细（按日/月/年聚合）"""
    if granularity not in ("daily", "monthly", "yearly"):
        granularity = "daily"
    return cost_tracker.get_history(granularity=granularity, limit=limit)


@router.get("/all-time", response_model=dict)
def all_time_cost():
    """获取全历史总花费（从 SQLite 查询，不受内存窗口限制）"""
    return cost_tracker.get_all_time_cost()


@router.get("/alerts", response_model=list)
def active_alerts():
    """获取当前活跃的预算告警"""
    return cost_tracker.get_active_alerts()


@router.put("/budget", response_model=dict)
def update_budget(
    daily: Optional[float] = Body(None, description="每日预算 USD"),
    monthly: Optional[float] = Body(None, description="每月预算 USD"),
):
    """更新预算限制"""
    return cost_tracker.update_budget(daily=daily, monthly=monthly)
