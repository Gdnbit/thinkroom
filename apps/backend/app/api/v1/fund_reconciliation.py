"""G3 · /api/v1/fund-reconciliation — 基金估值 EOD 对账.

端点
----
- GET  /pending?fund_code=&limit=200       列待填实际 NAV 的 EOD final 快照
- GET  /summary?fund_code=&since_days=30   误差统计（按 fund 分组）
- GET  /timeseries?fund_code=&since_days=30  对账明细（画曲线用）
- POST /{snapshot_id}/actual                 手动填入真实 NAV
- POST /run-eod                              手动触发 EOD 标记（一般由 scheduler 自动跑）
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services import fund_reconciliation as fr


router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic schemas ────────────────────────────────────────────────


class FillActualNavRequest(BaseModel):
    actual_nav: float = Field(..., gt=0, description="基金当日真实净值")


class ReconciliationSummaryOut(BaseModel):
    fund_code: str
    sample_count: int
    pending_count: int
    mae_pct: Optional[float] = None
    rmse_pct: Optional[float] = None
    mape_pct: Optional[float] = None
    bias_pct: Optional[float] = None
    max_error_pct: Optional[float] = None
    last_eod_at: Optional[str] = None
    since_days: int


# ── 端点 ─────────────────────────────────────────────────────────────


@router.get("/pending")
def list_pending(
    fund_code: Optional[str] = Query(None, description="可选：只筛某只 fund"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> List[dict]:
    """列待填实际 NAV 的 EOD final 快照（最新优先）."""
    return fr.get_pending_reconciliation(db, fund_code=fund_code, limit=limit)


@router.get("/summary", response_model=List[ReconciliationSummaryOut])
def summary(
    fund_code: Optional[str] = Query(None),
    since_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> List[ReconciliationSummaryOut]:
    """误差统计 — MAE / RMSE / MAPE / 系统性偏差."""
    summaries = fr.get_reconciliation_summary(
        db, fund_code=fund_code, since_days=since_days
    )
    return [ReconciliationSummaryOut(**s.__dict__) for s in summaries]


@router.get("/timeseries")
def timeseries(
    fund_code: str = Query(..., min_length=1),
    since_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> List[dict]:
    """对账明细（按时间顺序）— 用于前端画估值 vs 实际曲线."""
    return fr.get_reconciliation_timeseries(db, fund_code=fund_code, since_days=since_days)


@router.post("/{snapshot_id}/actual")
def fill_actual(
    req: FillActualNavRequest,
    snapshot_id: str = Path(..., min_length=10),
    db: Session = Depends(get_db),
) -> dict:
    """手动填入某 EOD final 快照的真实 NAV."""
    try:
        snap = fr.fill_actual_nav(db, snapshot_id, req.actual_nav)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    err_pct = None
    if snap.est_nav and snap.actual_nav:
        err_pct = round((snap.est_nav - snap.actual_nav) / snap.actual_nav * 100, 4)
    return {
        "id": snap.id,
        "fund_code": snap.fund_code,
        "ts": snap.ts.isoformat() if snap.ts else None,
        "est_nav": snap.est_nav,
        "actual_nav": snap.actual_nav,
        "error_pct": err_pct,
    }


@router.post("/run-eod")
def run_eod_now(db: Session = Depends(get_db)) -> dict:
    """手动触发 EOD 标记（一般由 scheduler 17:00 自动跑；此接口便于调试 / 补跑）."""
    return fr.mark_eod_finals(db)
