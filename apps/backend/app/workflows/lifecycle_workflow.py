"""Lifecycle Workflow

职责：批量扫描 active Claim，调用 LifecycleSkill 判断过期/失效，写入 lifecycle_logs。
供 scheduler.py 定时调用，也可通过 /api/v1/lifecycle/scan 手动触发。
"""
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from ulid import ULID
from app.database import safe_commit

from app.services.claim_repository import ClaimRepository
from app.skills.lifecycle_skill import LifecycleSkill, LifecycleScanInput
from app.schemas.claim_card import ClaimCard
from app.schemas.lifecycle_log import LifecycleLog


class LifecycleWorkflow:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ClaimRepository(db)
        self.skill = LifecycleSkill()

    def run_batch_check(self, limit: int = 50) -> dict:
        """批量扫描 active Claim，标记过期/失效，写 lifecycle_log"""
        now = datetime.now(timezone.utc)
        claims, total = self.repo.search(status="active", limit=limit)

        if not claims:
            return {"processed": 0, "expired": 0, "updated": 0, "active_remaining": 0}

        scan_output = self.skill.scan(LifecycleScanInput(
            claims=claims,
            current_time=now,
        ))

        applied = 0
        for upd in scan_output.updates:
            claim = self.db.query(ClaimCard).filter(ClaimCard.id == upd.claim_id).first()
            if not claim:
                continue
            old_status = claim.status
            claim.status = upd.new_status
            claim.expiry_reason = upd.reason
            claim.updated_at = now

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
            self.db.add(log)
            applied += 1

        if applied > 0:
            safe_commit(self.db)

        return {
            "processed": scan_output.scanned_count,
            "expired": scan_output.expired_count,
            "updated": applied,
            "active_remaining": scan_output.active_count,
        }
