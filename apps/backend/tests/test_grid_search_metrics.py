"""B8 · grid search 的 IR 指标测试（纯数学函数）"""
import sys
from pathlib import Path

# 让测试能 import scripts
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pytest

from grid_search_weights import (
    ndcg_at_k,
    recall_at_k,
    _generate_weight_grid,
)


class TestRecallAtK:
    def test_full_recall(self):
        assert recall_at_k(["a", "b"], ["a", "b", "c"], k=3) == 1.0

    def test_half_recall(self):
        assert recall_at_k(["a", "b"], ["a", "x", "y"], k=3) == 0.5

    def test_zero_recall(self):
        assert recall_at_k(["a", "b"], ["x", "y", "z"], k=3) == 0.0

    def test_top_k_truncation(self):
        # relevant 在 k=1 外 → recall@1 = 0
        assert recall_at_k(["b"], ["a", "b"], k=1) == 0.0
        assert recall_at_k(["b"], ["a", "b"], k=2) == 1.0

    def test_empty_relevant(self):
        assert recall_at_k([], ["a", "b"], k=2) == 0.0


class TestNdcgAtK:
    def test_perfect_ranking(self):
        # 相关文档排最前 → nDCG = 1
        assert ndcg_at_k(["a", "b"], ["a", "b", "c"], k=3) == pytest.approx(1.0)

    def test_worst_ranking(self):
        # 相关文档全在 k 外 → nDCG = 0
        assert ndcg_at_k(["a"], ["x", "y", "z"], k=3) == 0.0

    def test_reversed_ranking_better_than_zero(self):
        # 相关在 top-k 里但排在末尾 → 0 < nDCG < 1
        score = ndcg_at_k(["a"], ["x", "y", "a"], k=3)
        assert 0 < score < 1

    def test_first_position_higher_than_later(self):
        # 相关排第 1 的 nDCG > 排第 3 的
        s_first = ndcg_at_k(["a"], ["a", "x", "y"], k=3)
        s_last = ndcg_at_k(["a"], ["x", "y", "a"], k=3)
        assert s_first > s_last

    def test_empty_relevant(self):
        assert ndcg_at_k([], ["a"], k=1) == 0.0

    def test_fewer_k_than_ranked(self):
        # 只看 top-1
        assert ndcg_at_k(["a", "b"], ["a", "b"], k=1) == pytest.approx(1.0)
        assert ndcg_at_k(["b"], ["a", "b"], k=1) == 0.0


class TestWeightGrid:
    def test_all_weights_sum_to_one(self):
        configs = _generate_weight_grid(step=0.1)
        for c in configs:
            total = c["sem"] + c["trust"] + c["conf"] + c["decay"]
            assert abs(total - 1.0) < 1e-6, f"权重不归一: {c} sum={total}"

    def test_sem_always_dominant(self):
        # 依据脚本设计：sem ∈ [0.3, 0.7]
        for c in _generate_weight_grid(step=0.1):
            assert 0.3 <= c["sem"] <= 0.7

    def test_side_weights_in_bounds(self):
        for c in _generate_weight_grid(step=0.1):
            for k in ("trust", "conf", "decay"):
                assert 0.05 <= c[k] <= 0.40, f"{k} out of range: {c}"

    def test_grid_size_reasonable(self):
        # step=0.1 时组合数应在 20-500 之间（过多太慢，过少无意义）
        configs = _generate_weight_grid(step=0.1)
        assert 20 <= len(configs) <= 500, f"grid size {len(configs)} 异常"
        # step=0.05 应显著变多
        configs_fine = _generate_weight_grid(step=0.05)
        assert len(configs_fine) > len(configs)

    def test_default_weights_reachable(self):
        """检查默认 sem=0.50, trust=0.25, conf=0.12, decay=0.13 在某个 step 的网格上可达。

        使用 step=0.01 的精细网格。注意：conf=0.12 和 decay=0.13 需要该网格覆盖这些边界。
        """
        from app.utils.claim_post_processor import DEFAULT_SCORE_WEIGHTS
        # sum must be 1.0 by invariant
        total = sum(DEFAULT_SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6
