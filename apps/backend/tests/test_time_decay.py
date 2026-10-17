"""S4 验证：time_decay_factor 衰减曲线正确"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from app.utils.time_decay import time_decay_factor


_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)


class TestHalfLife:
    def test_fresh_claim_factor_near_one(self):
        """刚创建的 claim 应接近 1.0"""
        assert time_decay_factor(_NOW, "medium", now=_NOW) == pytest.approx(1.0, abs=0.01)

    def test_short_half_life_at_7days(self):
        """short horizon @ 7 days 应 = 0.5"""
        created = _NOW - timedelta(days=7)
        f = time_decay_factor(created, "short", now=_NOW)
        assert f == pytest.approx(0.5, abs=0.01)

    def test_medium_half_life_at_30days(self):
        """medium horizon @ 30 days 应 = 0.5"""
        created = _NOW - timedelta(days=30)
        f = time_decay_factor(created, "medium", now=_NOW)
        assert f == pytest.approx(0.5, abs=0.01)

    def test_long_half_life_at_180days(self):
        """long horizon @ 180 days 应 = 0.5"""
        created = _NOW - timedelta(days=180)
        f = time_decay_factor(created, "long", now=_NOW)
        assert f == pytest.approx(0.5, abs=0.01)

    def test_unspecified_default_half_life_at_60days(self):
        created = _NOW - timedelta(days=60)
        f = time_decay_factor(created, "unspecified", now=_NOW)
        assert f == pytest.approx(0.5, abs=0.01)


class TestEdgeCases:
    def test_non_time_sensitive_always_one(self):
        """time_sensitive=False（理念/框架）不衰减"""
        very_old = _NOW - timedelta(days=3650)  # 10 年前
        f = time_decay_factor(very_old, "medium", time_sensitive=False, now=_NOW)
        assert f == 1.0

    def test_none_created_at_returns_neutral(self):
        f = time_decay_factor(None, "medium", now=_NOW)
        assert f == 0.5

    def test_future_timestamp_returns_one(self):
        """防御：未来时间戳按最新算"""
        future = _NOW + timedelta(days=5)
        f = time_decay_factor(future, "medium", now=_NOW)
        assert f == 1.0

    def test_very_old_claim_floored_at_5pct(self):
        """极老 claim 不会完全消失（floor 0.05）"""
        ancient = _NOW - timedelta(days=3650)
        f = time_decay_factor(ancient, "short", now=_NOW)
        assert f >= 0.05
        assert f < 0.1  # 应该接近但不为 0

    def test_naive_datetime_handled(self):
        """naive datetime 自动补 UTC"""
        naive = (_NOW - timedelta(days=7)).replace(tzinfo=None)
        f = time_decay_factor(naive, "short", now=_NOW)
        assert f == pytest.approx(0.5, abs=0.01)

    def test_immediate_same_as_short(self):
        """immediate 和 short 共用 7 天半衰期"""
        created = _NOW - timedelta(days=7)
        f_imm = time_decay_factor(created, "immediate", now=_NOW)
        f_short = time_decay_factor(created, "short", now=_NOW)
        assert f_imm == pytest.approx(f_short, abs=0.001)


class TestMonotonic:
    def test_older_claims_score_lower(self):
        """同 horizon 下，越老的 claim 应得分越低"""
        scores = []
        for days in [1, 7, 14, 30, 60]:
            f = time_decay_factor(_NOW - timedelta(days=days), "medium", now=_NOW)
            scores.append(f)
        # 严格递减
        for i in range(len(scores) - 1):
            assert scores[i] > scores[i + 1]
