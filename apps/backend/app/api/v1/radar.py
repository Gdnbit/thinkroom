"""Daily Radar API — 每日雷达 / 主动发现

将 OpportunityScout 的扫描结果暴露为 REST 接口：
- GET /radar       — 获取最新扫描结果（从 watchlist 中的资产扫描）
- POST /radar/scan — 手动触发一次扫描
"""
from dataclasses import asdict
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.services.opportunity_scout import OpportunityScout

router = APIRouter()

# 内存缓存最近一次扫描结果（简单方案，无需额外基础设施）
_last_scan: dict = {"cards": [], "scanned_at": None, "assets": []}


@router.get("")
def get_radar(db: Session = Depends(get_db)):
    """获取最近一次雷达扫描结果"""
    if not _last_scan["cards"]:
        # 首次访问时自动触发一次
        _do_scan(db)
    return {
        "cards": _last_scan["cards"],
        "scanned_at": _last_scan["scanned_at"],
        "assets_scanned": _last_scan["assets"],
    }


@router.post("/scan")
def trigger_scan(
    assets: Optional[List[str]] = Query(None),
    db: Session = Depends(get_db),
):
    """手动触发雷达扫描"""
    _do_scan(db, force_assets=assets)
    return {
        "status": "ok",
        "cards_found": len(_last_scan["cards"]),
        "cards": _last_scan["cards"],
        "scanned_at": _last_scan["scanned_at"],
        "assets_scanned": _last_scan["assets"],
    }


def _do_scan(db: Session, force_assets: Optional[List[str]] = None):
    """执行扫描并更新缓存"""
    from datetime import datetime, timezone

    if force_assets:
        assets = [a.upper() for a in force_assets]
    else:
        from app.schemas.watchlist import WatchlistItem
        items = db.query(WatchlistItem).filter(WatchlistItem.status == "active").all()
        assets = list({i.item_key.upper() for i in items if i.item_key})

    if not assets:
        assets = ["BTC", "ETH"]  # fallback

    scout = OpportunityScout(db)
    cards = scout.scan(watchlist_assets=assets)
    _last_scan["cards"] = [asdict(c) for c in cards]
    _last_scan["scanned_at"] = datetime.now(timezone.utc).isoformat()
    _last_scan["assets"] = assets
