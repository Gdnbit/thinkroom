"""S4 · 时间衰减评分

为召回打分提供时间衰减因子，让"上周的观点"比"去年的观点"权重更高。

使用指数衰减公式：factor = exp(-ln(2) * age_days / half_life)
half_life 按 time_horizon 自适应：
    immediate / short  →  7 天
    medium             →  30 天
    long               →  180 天
    unspecified / None →  60 天（默认）

对 time_sensitive=False 的 claim（理念 / 框架），不衰减（factor=1.0）。

典型值参考：
    age=0      → 1.00
    age=half   → 0.50
    age=2*half → 0.25
    age=3*half → 0.125
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional


# time_horizon → half_life_days
_HALF_LIFE_MAP: dict[str, float] = {
    "immediate": 7.0,
    "short": 7.0,
    "medium": 30.0,
    "long": 180.0,
    "unspecified": 60.0,
}

_DEFAULT_HALF_LIFE = 60.0


def time_decay_factor(
    created_at: Optional[datetime],
    time_horizon: Optional[str] = None,
    time_sensitive: bool = True,
    now: Optional[datetime] = None,
) -> float:
    """返回 (0, 1] 区间的时间衰减因子。

    Args:
        created_at: claim 创建时间（UTC）；None 时返回 0.5（中性）
        time_horizon: "immediate" | "short" | "medium" | "long" | "unspecified" | None
        time_sensitive: 是否时效敏感；False 时总是返回 1.0（理念/框架不衰减）
        now: 参考时间点；None 时用 UTC now（可注入以便测试）

    Returns:
        float in (0, 1]
    """
    # 非时效敏感：不衰减
    if not time_sensitive:
        return 1.0

    if created_at is None:
        return 0.5  # 缺失时间 → 中性保守分

    now = now or datetime.now(timezone.utc)

    # 归一化 naive → UTC aware
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age_seconds = (now - created_at).total_seconds()
    if age_seconds <= 0:
        return 1.0  # 未来时间戳 → 视为"最新"

    age_days = age_seconds / 86400.0

    half_life = _HALF_LIFE_MAP.get((time_horizon or "").lower(), _DEFAULT_HALF_LIFE)
    # exp(-ln(2) * age / half_life)
    factor = math.exp(-math.log(2.0) * age_days / half_life)
    # 数值护栏：避免极老 claim 过分降权导致完全消失
    return max(factor, 0.05)


__all__ = ["time_decay_factor"]
