"""Contradiction Detector

职责：新 Claim 入库时，检测与同资产历史 active Claim 的矛盾。

规则（纯规则，无 LLM）：
- 同 asset_scope 重叠（大小写不敏感 + 常见别名展开）
- stance 方向相反（bullish ↔ bearish）
- time_horizon 相同或相近
- 置信度加权：新旧 Claim 置信度均 >= 0.5 才标记
"""
from typing import List, Optional, Set
from sqlalchemy.orm import Session
from app.schemas.claim_card import ClaimCard


OPPOSITE_STANCE = {
    "bullish": "bearish",
    "bearish": "bullish",
}

HORIZON_GROUPS = {
    "immediate": 0,
    "short": 1,
    "medium": 2,
    "long": 3,
    "unspecified": 1,
}

# 资产别名组：同组内的任意标签互相视为同一资产
_ALIAS_GROUPS = [
    {"btc", "bitcoin", "比特币"},
    {"eth", "ethereum", "以太坊"},
    {"sol", "solana"},
    {"bnb", "binance coin", "币安币"},
    {"xrp", "ripple", "瑞波"},
    {"doge", "dogecoin", "狗狗币"},
    {"ada", "cardano"},
    {"avax", "avalanche"},
    {"dot", "polkadot", "波卡"},
    {"link", "chainlink"},
    {"uni", "uniswap"},
    {"matic", "polygon"},
    {"oil", "原油", "石油", "crude oil", "wti", "brent"},
    {"gold", "黄金", "xau"},
    {"silver", "白银", "xag"},
    {"spx", "s&p500", "s&p 500", "标普500", "标普"},
    {"ndx", "nasdaq", "纳斯达克", "纳指"},
    {"dxy", "美元指数", "usd index"},
    {"fed", "美联储", "federal reserve"},
    {"cpi", "通胀", "inflation"},
]

# 构建 token → canonical key 的映射（启动时一次性构建）
_TOKEN_TO_CANONICAL: dict[str, str] = {}
for _group in _ALIAS_GROUPS:
    _canonical = min(_group)  # 取字母序最小的作为 canonical
    for _alias in _group:
        _TOKEN_TO_CANONICAL[_alias] = _canonical


def _normalize_asset_set(assets: List[str]) -> Set[str]:
    """将 asset_scope 标准化：小写 + 别名展开为 canonical key"""
    result = set()
    for a in assets:
        lower = a.strip().lower()
        result.add(lower)
        canonical = _TOKEN_TO_CANONICAL.get(lower)
        if canonical:
            result.add(canonical)
    return result


class ContradictionDetector:
    def __init__(self, db: Session):
        self.db = db

    def check_and_mark(self, new_claim: ClaimCard) -> List[str]:
        """检测新 Claim 与历史 Claim 的矛盾，返回被标记矛盾的 claim_id 列表"""
        if new_claim.stance not in OPPOSITE_STANCE:
            return []  # neutral/conditional 不参与矛盾检测

        if not new_claim.asset_scope:
            return []

        opposite = OPPOSITE_STANCE[new_claim.stance]
        new_horizon_group = HORIZON_GROUPS.get(new_claim.time_horizon or "unspecified", 1)
        new_assets = _normalize_asset_set(new_claim.asset_scope)

        # 查找同资产、相反方向、active 的历史 Claim
        candidates = (
            self.db.query(ClaimCard)
            .filter(
                ClaimCard.id != new_claim.id,
                ClaimCard.status == "active",
                ClaimCard.stance == opposite,
                ClaimCard.is_contradicted == False,  # noqa: E712
            )
            .all()
        )

        # 过滤：asset_scope 重叠 + time_horizon 相近 + 置信度足够
        marked = []
        for old in candidates:
            old_assets = _normalize_asset_set(old.asset_scope or [])
            if not (new_assets & old_assets):
                continue
            old_horizon_group = HORIZON_GROUPS.get(old.time_horizon or "unspecified", 1)
            if abs(new_horizon_group - old_horizon_group) > 1:
                continue
            if (new_claim.confidence or 0) < 0.5 or (old.confidence or 0) < 0.5:
                continue

            # 标记旧 Claim 为 contradicted
            old.is_contradicted = True
            marked.append(old.id)

        if marked:
            self.db.flush()

        return marked
