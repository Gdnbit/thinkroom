from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.event import EventCardResponse  # noqa: F401  保留以备 schema 扩展
from app.mcps.event_feed_mcp import EventFeedMCP
from app.services import market_persist as persist

router = APIRouter()


@router.get("", response_model=List[dict])
def fetch_events(
    asset: Optional[str] = Query(None, description="资产过滤，如 BTC"),
    theme: Optional[str] = Query(None, description="主题过滤，如 宏观"),
    importance_min: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(20, ge=1, le=100),
    force_live: bool = Query(
        False,
        description=(
            "强制走 live 抓取（Alpha Vantage + 财联社 + 金十 + RSS + 社媒，可能 30s+）。"
            "默认 false：仅查 DB 缓存（< 1s），DB 空才回退 live 抓取。"
        ),
    ),
    cache_max_age_minutes: int = Query(60, ge=1, le=1440, description="DB 缓存接受的最大年龄（分钟）"),
    db: Session = Depends(get_db),
):
    """获取事件列表 — 默认走 DB 缓存避免 30s 阻塞，`force_live=true` 才触发 live 抓取.

    数据流：
    - 默认（force_live=false）：先查 DB 缓存 → 有则秒返；无则降级 live 抓取
    - force_live=true：跳过缓存，强制走 EventFeedMCP 全链路（外网 + 配额）
    """
    # 默认路径：DB 缓存优先（避免外网 30s+ 阻塞）
    if not force_live:
        cached = persist.get_fresh_events(
            db, asset=asset, max_age_minutes=cache_max_age_minutes, limit=limit
        )
        if cached:
            filtered = [e for e in cached if e.get("importance", 0) >= importance_min]
            if theme:
                filtered = [
                    e for e in filtered
                    if theme in (e.get("theme_scope") or [])
                ]
            if filtered:
                # 字段名兼容：cache 用 occurred_at，MCP 用 event_timestamp，统一暴露两者
                for e in filtered:
                    if "event_timestamp" not in e and "occurred_at" in e:
                        e["event_timestamp"] = e["occurred_at"]
                return filtered[:limit]
        # DB 空：降级到 live 抓取（首次启动或长期无数据时）

    mcp = EventFeedMCP(db)
    resp = mcp.fetch_recent_events(
        asset=asset,
        theme=theme,
        importance_min=importance_min,
        limit=limit,
    )
    if not resp.success:
        return []
    return resp.data
