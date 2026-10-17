"""A6 验证：debate_seats helper 正确读写 V2 + V1"""
from __future__ import annotations

from types import SimpleNamespace
from app.utils.debate_seats import get_seat_model, build_seat_models, seat_models_dict


class TestBuildSeatModels:
    def test_basic(self):
        d = build_seat_models(baseline="a", probability="b", risk="c")
        assert d == {"baseline": "a", "probability": "b", "risk": "c"}

    def test_none_filtered(self):
        d = build_seat_models(baseline="a", probability=None, risk=None)
        assert d == {"baseline": "a"}

    def test_extras_supported(self):
        d = build_seat_models(baseline="a", audit="e", pnl="f")
        assert d == {"baseline": "a", "audit": "e", "pnl": "f"}


class TestReadV2First:
    def test_prefers_model_by_seat(self):
        """有 model_by_seat 时不读旧列"""
        debate = SimpleNamespace(
            model_by_seat={"baseline": "new_a", "risk": "new_c"},
            model_bull="old_a", model_bear="old_b", model_neutral="old_c",
        )
        assert get_seat_model(debate, "baseline") == "new_a"
        assert get_seat_model(debate, "risk") == "new_c"
        assert get_seat_model(debate, "probability") is None  # 新列没这个 key

    def test_v2_with_extra_seats(self):
        debate = SimpleNamespace(
            model_by_seat={"baseline": "a", "structure": "d", "audit": "e", "pnl": "f"},
        )
        assert get_seat_model(debate, "structure") == "d"
        assert get_seat_model(debate, "audit") == "e"
        assert get_seat_model(debate, "pnl") == "f"


class TestFallbackToV1:
    def test_empty_model_by_seat_falls_back(self):
        """新列为空 dict → 读旧列"""
        debate = SimpleNamespace(
            model_by_seat={},
            model_bull="old_a", model_bear="old_b", model_neutral="old_c",
        )
        assert get_seat_model(debate, "baseline") == "old_a"
        assert get_seat_model(debate, "probability") == "old_b"
        assert get_seat_model(debate, "risk") == "old_c"

    def test_none_model_by_seat_falls_back(self):
        debate = SimpleNamespace(
            model_by_seat=None,
            model_bull="old_a", model_bear="old_b", model_neutral="old_c",
        )
        d = seat_models_dict(debate)
        assert d == {"baseline": "old_a", "probability": "old_b", "risk": "old_c"}

    def test_missing_attr_model_by_seat(self):
        """完全没有新列的老 ORM 实例也安全 fallback"""
        class Legacy:
            model_bull = "x"
            model_bear = "y"
            model_neutral = "z"
        assert get_seat_model(Legacy(), "baseline") == "x"

    def test_empty_legacy_returns_none(self):
        debate = SimpleNamespace(
            model_by_seat={},
            model_bull=None, model_bear=None, model_neutral=None,
        )
        assert seat_models_dict(debate) == {}
        assert get_seat_model(debate, "baseline") is None


class TestDualWriteConsistency:
    """验证 workflow 代码的 dual-write 模式与 helper 期望一致"""

    def test_build_from_workflow_args(self):
        # 模拟 committee_workflow.py 里的调用
        _baseline = "committee_a"
        _probability = "committee_b"
        _risk = "committee_c"
        _structure = None
        result = build_seat_models(
            baseline=_baseline,
            probability=_probability,
            risk=_risk,
            structure=_structure,
        )
        # structure 为 None 被过滤
        assert result == {
            "baseline": "committee_a",
            "probability": "committee_b",
            "risk": "committee_c",
        }
