"""G17 自适应 top-K 单元测试

覆盖：
- ADAPTIVE_TOPK_ENABLED=False（默认）→ 完全保持现有 top_k 行为
- ENABLED=True：
  - 全高分（score 都 >= 阈值）→ 触发上限 clip 到 top_k * MAX_FACTOR
  - 全低分（score 都 < 阈值）→ 触发下限 clip 到 ADAPTIVE_TOPK_MIN
  - 阈值过严（候选少于 min_k）→ 退化用未过滤 scored[:min_k]
  - 阈值过松（候选超 max_k）→ 截到 max_k
  - 候选数 = top_k → 不算 applied
- 触发 HARD_MAX 绝对上限
- 空 candidates → 不崩

注意：
- score = sem*0.5 + trust*0.25 + conf*0.12 + decay*0.13
- sem 在没有 reranker 时 = 1.0 - idx / total（候选池排名分）
- 测试构造便宜的 fake claims，控制 confidence 和 trust（决定 score）
"""
from __future__ import annotations

import pytest

from app.utils.claim_post_processor import (
    PostProcessResult,
    post_process_candidates,
)


# ── Fake claim 构造 ──────────────────────────────────────────


class FakeClaim:
    """最小 ClaimCard 替身：只放后处理器需要的字段。"""

    def __init__(
        self,
        claim_id: str,
        confidence: float = 0.7,
        trust: float = 0.7,
        stance: str = "neutral",
        time_horizon: str = "short",
        is_contradicted: bool = False,
    ):
        self.id = claim_id
        self.summary = f"Mock summary {claim_id}"
        self.full_quote = f"Mock quote {claim_id}"
        self.confidence = confidence
        self.source_trust_weight = trust
        self.stance = stance
        self.time_horizon = time_horizon
        self.is_contradicted = is_contradicted
        self.time_sensitive = False  # 让 decay 稳定
        # 给一个固定的 created_at（使 time_decay 接近 1.0）
        from datetime import datetime, timezone
        self.created_at = datetime.now(timezone.utc)
        # 后处理器用到的其他可选属性
        self.asset_scope = ["BTC"]
        self.theme_scope = []
        self.speaker_id = None
        self.valid_until = None


def _make_pool(n: int, conf: float = 0.7, trust: float = 0.7) -> list:
    return [
        FakeClaim(f"c{i}", confidence=conf, trust=trust, stance="neutral")
        for i in range(n)
    ]


# ── 关闭开关：行为不变 ──────────────────────────────────────


def test_disabled_falls_back_to_top_k(monkeypatch):
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", False)
    pool = _make_pool(20)
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)
    assert len(r.claims) == 8
    assert r.adaptive_applied is False
    assert r.final_count == 8
    assert r.total_scanned == 20


def test_disabled_top_k_larger_than_pool(monkeypatch):
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", False)
    pool = _make_pool(3)
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)
    assert len(r.claims) == 3
    assert r.adaptive_applied is False


# ── 开启开关：所有候选都达标 → 触发上限 ──────────────────────


def test_enabled_high_score_pool_caps_at_max_factor(monkeypatch):
    """所有候选 score 都 >= 阈值 → 应 clip 到 top_k * MAX_FACTOR."""
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN_SCORE", 0.30)  # 低阈值
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN", 1)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MAX_FACTOR", 1.5)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_HARD_MAX", 100)

    # 30 条全高分（conf=trust=0.9）
    pool = _make_pool(30, conf=0.9, trust=0.9)
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)

    # max_k = min(8 * 1.5, 100) = 12
    assert len(r.claims) == 12
    assert r.adaptive_applied is True
    assert r.final_count == 12


def test_enabled_hard_max_overrides_factor(monkeypatch):
    """HARD_MAX 优先于 top_k * MAX_FACTOR."""
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN_SCORE", 0.0)  # 全过
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN", 1)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MAX_FACTOR", 5.0)  # 极大
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_HARD_MAX", 10)  # 但绝对上限 10

    pool = _make_pool(50)
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)

    # max_k = min(8 * 5.0, 10) = 10
    assert len(r.claims) == 10
    assert r.adaptive_applied is True


# ── 开启开关：低分被过滤 ───────────────────────────────────


def test_enabled_strict_threshold_falls_back_to_min(monkeypatch):
    """阈值很高 → 候选 0 条达标 → 退化给 min_k 条（来自未过滤 scored）."""
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN_SCORE", 0.99)  # 几乎不可达
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN", 2)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MAX_FACTOR", 1.5)

    pool = _make_pool(20, conf=0.5, trust=0.5)  # 中等分
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)

    # 阈值过严 → 给 min_k=2 条（未过滤 top）
    assert len(r.claims) == 2
    # adaptive_applied=False（因为退化路径不算 applied — 只是 min 兜底）
    assert r.adaptive_applied is False


def test_enabled_partial_qualifying_within_clip_range(monkeypatch):
    """部分候选达标，且在 [min_k, max_k] 范围内 → 直接返回 qualifying."""
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    # 用一个会让前 5 条达标的阈值
    # sem rank 给 5 条 0.50/0.25 trust/0.5 conf/decay≈1.0 的池子
    # 第 0 条：sem=1.0 → score = 0.5 + 0.25*0.5 + 0.12*0.5 + 0.13*1.0 = 0.815
    # 第 19 条：sem=0.05 → score = 0.025 + 0.125 + 0.06 + 0.13 = 0.34
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN_SCORE", 0.55)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN", 1)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MAX_FACTOR", 1.5)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_HARD_MAX", 50)

    pool = _make_pool(20, conf=0.5, trust=0.5)
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)

    # 应该返回少于 8 条（因为阈值过滤掉低分），但 >= min_k=1
    assert 1 <= len(r.claims) <= 12  # max_k = min(12, 50) = 12
    if len(r.claims) != 8:
        assert r.adaptive_applied is True
    assert r.final_count == len(r.claims)


def test_enabled_qualifying_equals_top_k_no_applied(monkeypatch):
    """阈值刚好让 8 条达标（= top_k）→ 不算 applied."""
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    # 简单方法：阈值 0 让所有候选都达标，但 max_factor=1.0 → max_k=top_k=8
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN_SCORE", 0.0)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN", 1)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MAX_FACTOR", 1.0)  # =1 → max=top_k
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_HARD_MAX", 100)

    pool = _make_pool(8)  # 候选 = top_k
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)

    assert len(r.claims) == 8
    assert r.adaptive_applied is False
    assert r.final_count == 8


# ── 边界 ─────────────────────────────────────────────────────


def test_enabled_empty_candidates_no_crash(monkeypatch):
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    r = post_process_candidates([], "query", top_k=8)
    assert r.claims == []
    assert r.adaptive_applied is False
    assert r.final_count == 0


def test_enabled_min_k_clamped_to_top_k(monkeypatch):
    """ADAPTIVE_TOPK_MIN 不应超过 caller 要求的 top_k."""
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN_SCORE", 0.99)  # 几乎过滤光
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN", 100)  # 远超 top_k
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MAX_FACTOR", 1.5)

    pool = _make_pool(20, conf=0.5, trust=0.5)
    r = post_process_candidates(pool, "query", top_k=3, use_stance_balance=False)

    # min_k = min(100, top_k=3) = 3
    assert len(r.claims) == 3


def test_final_count_matches_claims_length(monkeypatch):
    """final_count 应一致反映 claims 长度（无论是否 adaptive）."""
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_ENABLED", True)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MIN_SCORE", 0.0)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_MAX_FACTOR", 1.5)
    monkeypatch.setattr("app.config.settings.ADAPTIVE_TOPK_HARD_MAX", 100)

    pool = _make_pool(15)
    r = post_process_candidates(pool, "query", top_k=8, use_stance_balance=False)
    assert r.final_count == len(r.claims)


# ── 现有 PostProcessResult 字段不破坏 ──────────────────────────


def test_result_fields_backward_compatible():
    """新加 adaptive_applied / final_count 不应破坏现有 caller."""
    r = PostProcessResult()
    # 旧字段
    assert r.claims == []
    assert r.stance_distribution == {}
    assert r.balanced_applied is False
    assert r.reranker_applied is False
    assert r.total_scanned == 0
    # 新字段（默认值）
    assert r.adaptive_applied is False
    assert r.final_count == 0
