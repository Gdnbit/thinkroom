"""SpeakerProfile Repository"""
from typing import Optional, List
from sqlalchemy.orm import Session
from app.services.base_repository import BaseRepository
from app.schemas.speaker_profile import SpeakerProfile


class SpeakerRepository(BaseRepository[SpeakerProfile]):
    def __init__(self, db: Session):
        super().__init__(SpeakerProfile, db)

    def find_by_name(self, name: str) -> Optional[SpeakerProfile]:
        """按姓名精确查找"""
        return (
            self.db.query(SpeakerProfile)
            .filter(SpeakerProfile.name == name, SpeakerProfile.status == "active")
            .first()
        )

    def find_by_name_fuzzy(self, name: str) -> Optional[SpeakerProfile]:
        """按姓名模糊查找（含别名）"""
        # 精确匹配优先
        exact = self.find_by_name(name)
        if exact:
            return exact
        # 别名精确匹配
        all_active = self.db.query(SpeakerProfile).filter(SpeakerProfile.status == "active").all()
        for sp in all_active:
            if name in (sp.aliases or []):
                return sp
        # 模糊匹配 name 字段
        return (
            self.db.query(SpeakerProfile)
            .filter(
                SpeakerProfile.status == "active",
                SpeakerProfile.name.ilike(f"%{name}%"),
            )
            .first()
        )

    def find_by_alias(self, alias: str) -> Optional[SpeakerProfile]:
        """在所有 active speaker 的 aliases JSON 数组中查找"""
        all_active = self.db.query(SpeakerProfile).filter(SpeakerProfile.status == "active").all()
        for sp in all_active:
            if alias in (sp.aliases or []) or alias == sp.name:
                return sp
        return None

    def search(
        self,
        *,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[List[SpeakerProfile], int]:
        query = self.db.query(SpeakerProfile).filter(SpeakerProfile.status == "active")
        if name:
            query = query.filter(SpeakerProfile.name.ilike(f"%{name}%"))
        total = query.count()
        items = (
            query.order_by(SpeakerProfile.trust_weight.desc())
            .offset(skip).limit(limit).all()
        )
        # Python 端过滤 domain（JSON 数组）
        if domain:
            items = [s for s in items if domain in (s.domain_tags or [])]
        return items, total

    def increment_claim_count(self, speaker_id: str, verified: bool = False) -> None:
        """Claim 数量自增"""
        speaker = self.get(speaker_id)
        if speaker:
            speaker.total_claims = (speaker.total_claims or 0) + 1
            if verified:
                speaker.verified_claims = (speaker.verified_claims or 0) + 1
            self.db.flush()

    def update_hit_rate(self, speaker_id: str, hit_rate: float) -> None:
        """更新历史胜率"""
        speaker = self.get(speaker_id)
        if speaker:
            speaker.hit_rate = max(0.0, min(1.0, hit_rate))
            self.db.flush()
