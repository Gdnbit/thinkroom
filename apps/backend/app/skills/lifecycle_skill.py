"""Lifecycle Skill

职责：判断 ClaimCard 是否应该过期/失效/更新状态。

规则：
- 纯规则判断，不需要 LLM
- 检查时效性条件
- Skill 本身不写数据库，返回需要更新的 Claim 列表
"""
from typing import List, Optional, Any
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, ConfigDict
from app.schemas.claim_card import ClaimCard


class LifecycleUpdateItem(BaseModel):
    claim_id: str
    new_status: str
    reason: str
    triggered_by: str = "lifecycle_skill"


class LifecycleScanInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    claims: List[Any]  # List[ClaimCard] — SQLAlchemy model
    current_time: Optional[datetime] = None
    expiry_horizon_days: int = 90  # 超过多少天无更新则标记为过期


class LifecycleScanOutput(BaseModel):
    scanned_count: int
    updates: List[LifecycleUpdateItem]
    expired_count: int
    active_count: int


class LifecycleSkill:
    def scan(self, input: LifecycleScanInput) -> LifecycleScanOutput:
        """扫描 Claim 列表，返回需要更新状态的 Claim"""
        now = input.current_time or datetime.now(timezone.utc)
        updates: List[LifecycleUpdateItem] = []
        active_count = 0

        for claim in input.claims:
            if claim.status != "active":
                continue

            update = self._check_claim(claim, now, input.expiry_horizon_days)
            if update:
                updates.append(update)
            else:
                active_count += 1

        return LifecycleScanOutput(
            scanned_count=len(input.claims),
            updates=updates,
            expired_count=len(updates),
            active_count=active_count,
        )

    def _check_claim(
        self, claim: ClaimCard, now: datetime, horizon_days: int
    ) -> Optional[LifecycleUpdateItem]:
        """检查单个 Claim 是否需要更新状态"""

        # 1. 检查显式 valid_until
        if claim.valid_until:
            vu = claim.valid_until
            if vu.tzinfo is None:
                vu = vu.replace(tzinfo=timezone.utc)
            if vu < now:
                return LifecycleUpdateItem(
                    claim_id=claim.id,
                    new_status="expired",
                    reason=f"valid_until ({vu.date()}) has passed",
                )

        # 2. 根据 time_horizon 推断隐式过期
        horizon_map = {
            "immediate": 3,
            "short": 30,
            "medium": 90,
            "long": 365,
            "unspecified": horizon_days,
        }
        horizon = claim.time_horizon or "unspecified"
        max_days = horizon_map.get(horizon, horizon_days)

        created = claim.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (now - created).days

        if age_days > max_days:
            return LifecycleUpdateItem(
                claim_id=claim.id,
                new_status="expired",
                reason=f"Exceeded time_horizon ({horizon}, {max_days}d). Age: {age_days}d",
            )

        # 3. 检查是否被矛盾标记
        if claim.is_contradicted:
            return LifecycleUpdateItem(
                claim_id=claim.id,
                new_status="invalidated",
                reason="Marked as contradicted",
            )

        return None

    def check_single(self, claim: ClaimCard) -> Optional[LifecycleUpdateItem]:
        """检查单个 Claim"""
        now = datetime.now(timezone.utc)
        return self._check_claim(claim, now, 90)
