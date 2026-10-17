"""A4 验证：reranker 接口 + feature flag 降级"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
from app.utils.reranker import get_reranker, reset_reranker, _BGEReranker


class TestFeatureFlag:
    def setup_method(self):
        reset_reranker()

    def teardown_method(self):
        reset_reranker()

    def test_disabled_returns_none(self):
        """ENABLE_RERANKER=False 时 get_reranker() 返回 None，调用方应降级"""
        from app import config
        with patch.object(config.settings, "ENABLE_RERANKER", False):
            assert get_reranker() is None

    def test_enabled_returns_reranker_instance(self):
        """ENABLE_RERANKER=True 返回 _BGEReranker 实例（不强制加载模型）"""
        from app import config
        with patch.object(config.settings, "ENABLE_RERANKER", True):
            r = get_reranker()
            assert r is not None
            assert isinstance(r, _BGEReranker)


class TestBGERerankerWithoutModel:
    """无模型 / 无 FlagEmbedding 依赖时应降级到原序"""

    def test_rerank_empty_candidates(self):
        r = _BGEReranker("BAAI/bge-reranker-v2-m3")
        assert r.rerank("query", []) == []

    def test_rerank_without_flagembedding_preserves_rank(self):
        """FlagEmbedding 未装 → _ensure_loaded False → 降级到"按初级召回排名的归一分数"。

        修复前返回统一 0.5 会抹平排名信息，让调用方 (claim_post_processor._score)
        在 `sem * 0.5 + trust * 0.25 + ...` 融合里完全丢失语义相关性维度。
        现在降级时返回 1.0 - idx/n，和调用方的 else 分支行为一致。
        """
        r = _BGEReranker("BAAI/bge-reranker-v2-m3")
        # 模拟 import 失败：让 _ensure_loaded 返回 False
        r._ensure_loaded = lambda: False
        result = r.rerank("BTC 怎么看", ["a", "b", "c"])
        assert len(result) == 3
        # 顺序保留为原始 index
        assert [i for i, _ in result] == [0, 1, 2]
        # 分数单调递减（1.0, 2/3, 1/3）并落在 [0, 1]（浮点近似）
        import pytest
        scores = [s for _, s in result]
        assert scores == pytest.approx([1.0, 2 / 3, 1 / 3])
        assert scores[0] > scores[1] > scores[2]


class TestBGERerankerWithMockModel:
    def test_rerank_sorts_by_model_score(self):
        """模拟 FlagReranker，验证结果按分数降序"""
        r = _BGEReranker("mock")
        mock_impl = MagicMock()
        mock_impl.compute_score.return_value = [0.3, 0.9, 0.5]  # 索引 0, 1, 2 的分数
        r._impl = mock_impl
        r._ensure_loaded = lambda: True  # 直接跳过加载

        result = r.rerank("query", ["low", "high", "mid"])
        # 应按分数降序排：[(1, 0.9), (2, 0.5), (0, 0.3)]
        assert result == [(1, 0.9), (2, 0.5), (0, 0.3)]
        mock_impl.compute_score.assert_called_once()

    def test_rerank_handles_exception(self):
        """compute_score 抛异常 → 降级到"按初级召回排名的归一分数"。

        同 test_rerank_without_flagembedding_preserves_rank 的理由，保留初级排名
        优于统一 0.5。
        """
        r = _BGEReranker("mock")
        mock_impl = MagicMock()
        mock_impl.compute_score.side_effect = RuntimeError("gpu oom")
        r._impl = mock_impl
        r._ensure_loaded = lambda: True

        result = r.rerank("q", ["a", "b"])
        assert len(result) == 2
        # 顺序保留；分数单调递减
        assert [i for i, _ in result] == [0, 1]
        scores = [s for _, s in result]
        assert scores[0] > scores[1]
        assert scores == [1.0, 0.5]

    def test_rerank_single_candidate_scalar_result(self):
        """FlagReranker 单条时可能返回 scalar；应被转成 list"""
        r = _BGEReranker("mock")
        mock_impl = MagicMock()
        mock_impl.compute_score.return_value = 0.7  # scalar
        r._impl = mock_impl
        r._ensure_loaded = lambda: True

        result = r.rerank("q", ["only"])
        assert result == [(0, 0.7)]


class TestSingletonLifecycle:
    def setup_method(self):
        reset_reranker()

    def test_returns_same_instance(self):
        from app import config
        with patch.object(config.settings, "ENABLE_RERANKER", True):
            r1 = get_reranker()
            r2 = get_reranker()
            assert r1 is r2

    def test_reset_clears_singleton(self):
        from app import config
        with patch.object(config.settings, "ENABLE_RERANKER", True):
            r1 = get_reranker()
            reset_reranker()
            r2 = get_reranker()
            # 不是同一实例
            assert r1 is not r2
