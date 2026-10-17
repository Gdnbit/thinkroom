"""OutcomeRecord Repository"""
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.database import safe_commit
from app.services.base_repository import BaseRepository
from app.schemas.outcome_record import OutcomeRecord


class OutcomeRepository(BaseRepository[OutcomeRecord]):
    def __init__(self, db: Session):
        super().__init__(OutcomeRecord, db)

    def list(
        self,
        *,
        briefing_id: Optional[str] = None,
        claim_id: Optional[str] = None,
        outcome_type: Optional[str] = None,
        is_correct: Optional[bool] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[List[OutcomeRecord], int]:
        query = self.db.query(OutcomeRecord)
        if briefing_id:
            query = query.filter(OutcomeRecord.briefing_id == briefing_id)
        if claim_id:
            query = query.filter(OutcomeRecord.claim_id == claim_id)
        if outcome_type:
            query = query.filter(OutcomeRecord.outcome_type == outcome_type)
        if is_correct is not None:
            query = query.filter(OutcomeRecord.is_correct == is_correct)
        total = query.count()
        items = query.order_by(OutcomeRecord.created_at.desc()).offset(skip).limit(limit).all()
        return items, total

    def get_stats(self) -> dict:
        all_records = self.db.query(OutcomeRecord).all()
        total = len(all_records)
        hit = sum(1 for r in all_records if r.outcome_type == "hit")
        miss = sum(1 for r in all_records if r.outcome_type == "miss")
        partial = sum(1 for r in all_records if r.outcome_type == "partial_hit")
        not_triggered = sum(1 for r in all_records if r.outcome_type == "not_triggered")
        invalidated = sum(1 for r in all_records if r.outcome_type == "invalidated")

        judged = hit + miss + partial
        hit_rate = round(hit / judged, 4) if judged > 0 else 0.0

        with_correct = [r for r in all_records if r.is_correct is not None]
        correct_count = sum(1 for r in with_correct if r.is_correct)
        correct_rate = round(correct_count / len(with_correct), 4) if with_correct else 0.0

        pnl_values = [r.pnl_pct for r in all_records if r.pnl_pct is not None]
        avg_pnl = round(sum(pnl_values) / len(pnl_values), 4) if pnl_values else None

        return {
            "total": total,
            "hit_count": hit,
            "miss_count": miss,
            "partial_hit_count": partial,
            "not_triggered_count": not_triggered,
            "invalidated_count": invalidated,
            "hit_rate": hit_rate,
            "correct_rate": correct_rate,
            "avg_pnl_pct": avg_pnl,
        }

    def get_speaker_hit_rate(self, speaker_id: str) -> float:
        """计算指定来源的命中率（用于更新 SpeakerProfile.hit_rate）"""
        from app.schemas.claim_card import ClaimCard
        # 找该 speaker 关联的所有 claim_id
        claim_ids = [
            c.id for c in
            self.db.query(ClaimCard).filter(ClaimCard.speaker_id == speaker_id).all()
        ]
        if not claim_ids:
            return 0.0
        records = [
            r for r in self.db.query(OutcomeRecord).all()
            if r.claim_id in claim_ids and r.outcome_type in ("hit", "miss", "partial_hit")
        ]
        if not records:
            return 0.0
        hits = sum(1 for r in records if r.outcome_type == "hit")
        return round(hits / len(records), 4)

    def score_and_update_speaker(self, outcome_id: str) -> dict:
        """记录 Outcome 后，自动更新关联 Speaker 的胜率"""
        record = self.get(outcome_id)
        if not record or not record.claim_id:
            return {"updated": False, "reason": "no claim linked"}

        from app.schemas.claim_card import ClaimCard
        from app.schemas.speaker_profile import SpeakerProfile

        claim = self.db.get(ClaimCard, record.claim_id)
        if not claim or not claim.speaker_id:
            return {"updated": False, "reason": "claim has no speaker"}

        new_rate = self.get_speaker_hit_rate(claim.speaker_id)
        speaker = self.db.get(SpeakerProfile, claim.speaker_id)
        if speaker:
            speaker.hit_rate = new_rate
            self.db.flush()
            safe_commit(self.db)
            return {"updated": True, "speaker_id": claim.speaker_id, "new_hit_rate": new_rate}
        return {"updated": False, "reason": "speaker not found"}
