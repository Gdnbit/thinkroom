"""G5 · /api/v1/quality-monitor — 数据源历史质量监控.

与 `/api/v1/datasources/dashboard` 区分：
- /datasources：实时（内存计数器）+ 数据源元数据
- /quality-monitor：DB 历史聚合（持久化的 source_health_logs 表）

端点
----
- GET /summary?since_days=7         整体统计
- GET /by-source?since_days=7       按 source_name 分组
- GET /by-symbol?since_days=7       按 symbol 分组（找异常资产）
- GET /timeseries?since_days=7&bucket_hours=4   时间序列（画图用）
- GET /errors?limit=50              最近错误
- GET /error-categories?since_days=7  错误类型分布
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services import quality_monitor as qm


router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/summary")
def summary(
    since_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> dict:
    """整体统计（成功率 + 延迟分布）."""
    return qm.get_quality_summary(db, since_days=since_days)


@router.get("/by-source")
def by_source(
    since_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> List[dict]:
    """按数据源（source_name）分组的成功率与延迟."""
    return qm.get_quality_by_source(db, since_days=since_days)


@router.get("/by-symbol")
def by_symbol(
    since_days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[dict]:
    """按 symbol（资产代码）分组 — 优先列出失败率最高的."""
    return qm.get_quality_by_symbol(db, since_days=since_days, limit=limit)


@router.get("/timeseries")
def timeseries(
    since_days: int = Query(7, ge=1, le=90),
    bucket_hours: int = Query(4, ge=1, le=72),
    db: Session = Depends(get_db),
) -> List[dict]:
    """按时间桶聚合的成功率/延迟（画时间趋势图）."""
    return qm.get_quality_timeseries(
        db, since_days=since_days, bucket_hours=bucket_hours
    )


@router.get("/errors")
def errors(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[dict]:
    """最近的失败记录（运营定位故障用）."""
    return qm.get_recent_errors(db, limit=limit)


@router.get("/error-categories")
def error_categories(
    since_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> List[dict]:
    """错误类型分布（按 timeout / connection / 5xx 等启发式分类）."""
    return qm.get_error_categories(db, since_days=since_days)
