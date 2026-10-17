"""Snapshots API — 市场快照手动触发与状态查询

- POST /snapshots/trigger — 手动触发一次全量市场快照（含 Pizza/Fear&Greed/Polymarket）
- GET  /snapshots/status  — 查看最后一次快照采集的结果与触发来源
"""
from typing import Optional, List

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.scheduler import snapshot_capture_job, get_last_snapshot_info


router = APIRouter()


class TriggerResponse(BaseModel):
    status: str
    message: str
    triggered_by: str
    last: dict


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_snapshot(
    symbols: Optional[List[str]] = Query(None, description="可选：只刷新指定 symbol 子集"),
    include_indicators: bool = Query(True, description="是否一并刷新 Pizza / Fear&Greed / Polymarket"),
):
    """手动触发市场快照采集。

    同步阻塞，直到本次快照完成后返回（便于前端按钮获得明确反馈）。
    """
    await snapshot_capture_job(
        force_symbols=[s.upper() for s in symbols] if symbols else None,
        include_indicators=include_indicators,
        triggered_by="manual",
    )
    last = get_last_snapshot_info()
    return TriggerResponse(
        status="ok",
        message="snapshot capture completed",
        triggered_by="manual",
        last=last,
    )


@router.get("/status")
def get_snapshot_status():
    """查看最后一次快照采集的结果（不触发新抓取）。"""
    return get_last_snapshot_info()
