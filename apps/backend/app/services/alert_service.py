"""Alert Service

职责：扫描数据库，生成各类提醒事项：
- Lifecycle 到期的 Claim
- 高分 SetupCard（overall_score >= 0.7）
- 已完成的辩论
- 待复盘的 Briefing（created_at 超过 horizon_days）
"""
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from app.schemas.claim_card import ClaimCard
from app.schemas.setup_card import SetupCard
from app.schemas.briefing_card import BriefingCard
from app.schemas.debate import DebateRecord


ALERT_TYPES = {
    "claim_expiring": "观点即将到期",
    "high_score_setup": "高分 Setup 值得关注",
    "debate_completed": "委员会辩论已完成",
    "briefing_review_due": "简报可以复盘了",
    "claim_expired": "观点已过期",
}


class AlertService:
    def __init__(self, db: Session):
        self.db = db

    def get_all_alerts(self) -> list[dict]:
        """聚合所有当前提醒"""
        alerts = []
        alerts.extend(self._expiring_claims())
        alerts.extend(self._high_score_setups())
        alerts.extend(self._recent_debates())
        alerts.extend(self._briefings_due_review())
        # 按时间倒序，最多返回 20 条
        alerts.sort(key=lambda a: a["created_at"], reverse=True)
        return alerts[:20]

    def _expiring_claims(self) -> list[dict]:
        """找出 3 天内将到期的活跃 Claim"""
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=3)
        try:
            claims = (
                self.db.query(ClaimCard)
                .filter(
                    ClaimCard.status == "active",
                    ClaimCard.valid_until != None,  # noqa
                    ClaimCard.valid_until <= soon,
                    ClaimCard.valid_until > now,
                )
                .order_by(ClaimCard.valid_until)
                .limit(5)
                .all()
            )
            return [
                {
                    "id": f"expiring_{c.id}",
                    "type": "claim_expiring",
                    "title": ALERT_TYPES["claim_expiring"],
                    "message": f"{c.summary[:60]}… 将于 {c.valid_until.strftime('%m-%d') if c.valid_until else '?'} 到期",
                    "severity": "warning",
                    "related_id": c.id,
                    "related_type": "claim",
                    "created_at": now.isoformat(),
                }
                for c in claims
            ]
        except Exception:
            return []

    def _high_score_setups(self) -> list[dict]:
        """找出近 7 天内创建的高分 SetupCard"""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=7)
        try:
            setups = (
                self.db.query(SetupCard)
                .filter(
                    SetupCard.status == "active",
                    SetupCard.overall_score >= 0.7,
                    SetupCard.created_at >= cutoff,
                )
                .order_by(SetupCard.overall_score.desc())
                .limit(3)
                .all()
            )
            return [
                {
                    "id": f"setup_{s.id}",
                    "type": "high_score_setup",
                    "title": ALERT_TYPES["high_score_setup"],
                    "message": f"{s.asset} {s.direction} — 综合评分 {round((s.overall_score or 0)*100)}%，建议：{s.recommended_action}",
                    "severity": "info",
                    "related_id": s.id,
                    "related_type": "setup",
                    "created_at": s.created_at.isoformat() if s.created_at else now.isoformat(),
                }
                for s in setups
            ]
        except Exception:
            return []

    def _recent_debates(self) -> list[dict]:
        """找出近 24 小时内完成的辩论"""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        try:
            debates = (
                self.db.query(DebateRecord)
                .filter(
                    DebateRecord.status == "completed",
                    DebateRecord.completed_at >= cutoff,
                )
                .order_by(DebateRecord.completed_at.desc())
                .limit(3)
                .all()
            )
            return [
                {
                    "id": f"debate_{d.id}",
                    "type": "debate_completed",
                    "title": ALERT_TYPES["debate_completed"],
                    "message": f"{d.asset} | {d.final_direction} · {d.consensus_level} — {d.question[:50]}…",
                    "severity": "success",
                    "related_id": d.id,
                    "related_type": "debate",
                    "created_at": d.completed_at.isoformat() if d.completed_at else now.isoformat(),
                }
                for d in debates
            ]
        except Exception:
            return []

    def _briefings_due_review(self) -> list[dict]:
        """找出创建超过 7 天、仍为 active 的 Briefing（可以复盘了）"""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=7)
        try:
            briefings = (
                self.db.query(BriefingCard)
                .filter(
                    BriefingCard.status == "active",
                    BriefingCard.created_at <= cutoff,
                )
                .order_by(BriefingCard.created_at)
                .limit(3)
                .all()
            )
            return [
                {
                    "id": f"briefing_{b.id}",
                    "type": "briefing_review_due",
                    "title": ALERT_TYPES["briefing_review_due"],
                    "message": f"{b.title[:60]} — 已过 {(now - b.created_at.replace(tzinfo=timezone.utc)).days} 天",
                    "severity": "info",
                    "related_id": b.id,
                    "related_type": "briefing",
                    "created_at": b.created_at.isoformat() if b.created_at else now.isoformat(),
                }
                for b in briefings
            ]
        except Exception:
            return []
