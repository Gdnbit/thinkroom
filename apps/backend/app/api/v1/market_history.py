from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.services.snapshot_service import (
    capture_market_snapshots,
    get_default_symbols,
    get_intraday_window,
    get_snapshot_at_or_before,
    sync_market_state_into_snapshots,
)
from app.schemas.market_snapshot import FundEstimateSnapshot

router = APIRouter()


@router.post("/snapshots/capture")
async def capture_snapshots(
    symbols: Optional[str] = Query(None, description="逗号分隔 symbol，如 BTC,SPX,CSI300"),
    timeframe: str = Query("1d", description="抓取使用的时间框架"),
    db: Session = Depends(get_db),
):
    symbol_list = [s.strip().upper() for s in symbols.split(",")] if symbols else get_default_symbols(db)
    result = capture_market_snapshots(db, symbols=symbol_list, timeframe=timeframe)
    return {
        "success": True,
        "requested": result.requested,
        "saved": result.saved,
        "failed": result.failed,
        "estimated_funds": result.estimated_funds,
        "symbols": symbol_list,
    }


@router.get("/snapshots/point")
async def get_snapshot_point(
    symbol: str = Query(..., description="symbol，如 BTC"),
    at: Optional[str] = Query(None, description="ISO 时间，不传则默认当前 UTC"),
    db: Session = Depends(get_db),
):
    ts = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    data = get_snapshot_at_or_before(db, symbol=symbol.upper(), ts=ts)
    return data or {}


@router.get("/snapshots/window")
async def get_snapshot_window(
    symbol: str = Query(..., description="symbol，如 BTC"),
    minutes: int = Query(60, ge=1, le=1440),
    db: Session = Depends(get_db),
):
    return get_intraday_window(db, symbol=symbol.upper(), minutes=minutes)


@router.post("/snapshots/backfill-from-market-state")
async def backfill_market_state(
    limit: int = Query(300, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    inserted = sync_market_state_into_snapshots(db, limit=limit)
    return {"success": True, "inserted": inserted}


@router.get("/funds/estimate/latest")
async def get_latest_fund_estimate(
    fund_code: str = Query(..., description="基金代码"),
    db: Session = Depends(get_db),
):
    row = (
        db.query(FundEstimateSnapshot)
        .filter(FundEstimateSnapshot.fund_code == fund_code)
        .order_by(FundEstimateSnapshot.ts.desc())
        .first()
    )
    if not row:
        return {}
    return {
        "fund_code": row.fund_code,
        "ts": row.ts.isoformat() if row.ts else None,
        "est_nav": row.est_nav,
        "est_pct_change_1d": row.est_pct_change_1d,
        "confidence_level": row.confidence_level,
        "ref_index": row.ref_index,
        "model_version": row.model_version,
        "meta": row.meta,
    }
