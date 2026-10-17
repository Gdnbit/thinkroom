"""Lifecycle API — Claim 生命周期管理"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ulid import ULID

from app.database import safe_commit
from app.dependencies import get_db
from app.services.claim_repository import ClaimRepository
from app.skills.lifecycle_skill import LifecycleSkill, LifecycleScanInput
from app.schemas.claim_card import ClaimCard
from app.schemas.lifecycle_log import LifecycleLog

router = APIRouter()


@router.post("/scan")
def scan_lifecycle(
    dry_run: bool = Query(False, description="True=只检查不写库"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """扫描所有 active Claim，对过期/失效的进行状态变更并写 lifecycle_logs。"""
    repo = ClaimRepository(db)
    skill = LifecycleSkill()

    # 取所有 active claims
    claims, total = repo.search(status="active", limit=limit)

    scan_output = skill.scan(LifecycleScanInput(
        claims=claims,
        current_time=datetime.now(timezone.utc),
    ))

    applied = []
    now = datetime.now(timezone.utc)

    if not dry_run:
        for upd in scan_output.updates:
            claim = db.query(ClaimCard).filter(ClaimCard.id == upd.claim_id).first()
            if not claim:
                continue

            old_status = claim.status
            claim.status = upd.new_status
            claim.expiry_reason = upd.reason
            claim.updated_at = now

            # 写 lifecycle_log
            log = LifecycleLog(
                id=str(ULID()),
                claim_id=upd.claim_id,
                from_status=old_status,
                to_status=upd.new_status,
                reason=upd.reason,
                triggered_by=upd.triggered_by,
                evidence_snapshot={},
                occurred_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(log)
            applied.append({
                "claim_id": upd.claim_id,
                "from_status": old_status,
                "to_status": upd.new_status,
                "reason": upd.reason,
            })

        safe_commit(db)

    return {
        "scanned": scan_output.scanned_count,
        "expired": scan_output.expired_count,
        "updated": len(applied),
        "active_remaining": scan_output.active_count,
        "dry_run": dry_run,
        "applied": applied,
    }


class InvalidateRequest(BaseModel):
    reason: str = "manual invalidation"


@router.post("/invalidate/{claim_id}")
def invalidate_claim(
    claim_id: str,
    body: InvalidateRequest,
    db: Session = Depends(get_db),
):
    reason = body.reason
    """手动将 Claim 标记为 invalidated，写 lifecycle_log。"""
    claim = db.query(ClaimCard).filter(ClaimCard.id == claim_id).first()
    if not claim:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Claim not found")

    now = datetime.now(timezone.utc)
    old_status = claim.status
    claim.status = "invalidated"
    claim.is_contradicted = True
    claim.expiry_reason = reason
    claim.updated_at = now

    log = LifecycleLog(
        id=str(ULID()),
        claim_id=claim_id,
        from_status=old_status,
        to_status="invalidated",
        reason=reason,
        triggered_by="api_manual",
        evidence_snapshot={},
        occurred_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(log)
    safe_commit(db)

    return {"success": True, "claim_id": claim_id, "from_status": old_status, "to_status": "invalidated", "reason": reason}


@router.get("/logs/{claim_id}")
def get_lifecycle_logs(
    claim_id: str,
    db: Session = Depends(get_db),
):
    """获取某 Claim 的生命周期变化记录。"""
    logs = db.query(LifecycleLog).filter(
        LifecycleLog.claim_id == claim_id
    ).order_by(LifecycleLog.occurred_at.desc()).all()

    return [
        {
            "id": l.id,
            "claim_id": l.claim_id,
            "from_status": l.from_status,
            "to_status": l.to_status,
            "reason": l.reason,
            "triggered_by": l.triggered_by,
            "occurred_at": l.occurred_at.isoformat() if l.occurred_at else None,
        }
        for l in logs
    ]
