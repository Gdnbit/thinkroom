from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.dependencies import get_db
from app.services.alert_service import AlertService

router = APIRouter()

# 内存中记录已关闭的提醒 ID（轻量方案，重启后重置）
_dismissed: set[str] = set()


@router.get("", response_model=dict)
async def get_alerts(db: Session = Depends(get_db)):
    """获取当前所有提醒事项"""
    service = AlertService(db)
    alerts = service.get_all_alerts()
    # 过滤掉已关闭的
    alerts = [a for a in alerts if a.get('id') not in _dismissed]
    return {
        "alerts": alerts,
        "total": len(alerts),
    }


@router.post("/dismiss/{alert_id}")
async def dismiss_alert(alert_id: str):
    """关闭/标记已读某条提醒"""
    _dismissed.add(alert_id)
    return {"success": True}


@router.post("/dismiss-all")
async def dismiss_all_alerts(db: Session = Depends(get_db)):
    """关闭所有当前提醒"""
    service = AlertService(db)
    alerts = service.get_all_alerts()
    for a in alerts:
        if a.get('id'):
            _dismissed.add(a['id'])
    return {"success": True, "dismissed": len(alerts)}
