"""B1 · token_budget 单测：确保上下文预算器正确截断且保底"""
from app.utils.token_budget import (
    estimate_tokens,
    fit_blocks_to_budget,
    DEFAULT_CONTEXT_BUDGET,
)


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_pure_english(self):
        # "hello world" 11 chars → 11/4 + 1 ≈ 3-4 tokens
        t = estimate_tokens("hello world")
        assert 2 <= t <= 5

    def test_pure_chinese(self):
        # 10 个中文字符 → ~10-11 tokens（CJK 1:1 + 尾部 +1）
        t = estimate_tokens("一二三四五六七八九十")
        assert 10 <= t <= 12

    def test_mixed(self):
        # 5 中 + 20 英文 ≈ 5 + 5 + 1 ≈ 11
        t = estimate_tokens("比特币上涨 bitcoin rising today hi")
        assert t >= 10

    def test_japanese_katakana(self):
        # 平假名/片假名应按 CJK 计
        t = estimate_tokens("こんにちは")  # 5 字符
        assert 5 <= t <= 7

    def test_overestimate_safe(self):
        # 保守估算：同文本实际 BPE token 数通常 ≤ estimate
        text = "BTC 是 bitcoin 的缩写，当前价格 65000 美元。"
        # 17 CJK + 18 非CJK ≈ 17 + 4 + 1 = 22
        t = estimate_tokens(text)
        assert t >= 15  # 留足余量


class TestFitBlocksToBudget:
    def test_all_fit(self):
        blocks = ["aaa" * 10, "bbb" * 10, "ccc" * 10]  # each ~8 tokens
        kept, used, dropped = fit_blocks_to_budget(blocks, budget_tokens=100)
        assert kept == blocks
        assert dropped == 0
        assert used > 0

    def test_budget_exceeded_truncates(self):
        # 每块约 201 token (200 CJK + 1)，预算 500 → 应保留 2 块 (2×201=402)，
        # 第 3 块会让总数达到 603 > 500 被拒
        blocks = ["一" * 200, "二" * 200, "三" * 200, "四" * 200]
        kept, used, dropped = fit_blocks_to_budget(blocks, budget_tokens=500)
        assert len(kept) == 2
        assert dropped == 2
        assert used <= 500

    def test_keep_first_respected_even_if_over_budget(self):
        # 单块已超预算，但 keep_first=1 必须保留
        blocks = ["一" * 2000]  # ~2000 tokens
        kept, used, dropped = fit_blocks_to_budget(blocks, budget_tokens=500, keep_first=1)
        assert len(kept) == 1
        assert dropped == 0
        assert used >= 2000

    def test_empty_input(self):
        kept, used, dropped = fit_blocks_to_budget([], budget_tokens=100)
        assert kept == []
        assert used == 0
        assert dropped == 0

    def test_zero_budget_returns_first(self):
        blocks = ["hi", "there"]
        kept, used, dropped = fit_blocks_to_budget(blocks, budget_tokens=0)
        assert len(kept) == 1
        assert dropped == 1

    def test_preserves_order(self):
        # 高分块先来，预算中途耗尽，保留的必须是前缀
        blocks = [f"block{i} " + "内容" * 50 for i in range(8)]  # each ~100+ tokens
        kept, used, dropped = fit_blocks_to_budget(blocks, budget_tokens=300)
        # 保留的应当是 blocks[:N]
        for i, k in enumerate(kept):
            assert k == blocks[i]
        assert len(kept) + dropped == len(blocks)

    def test_default_budget_realistic(self):
        # 10 条典型 claim，每条 300 CJK 字符 ≈ 300 token；预算 4000
        # 预期保留 ~13 条（都进得去）
        blocks = ["一" * 300] * 10
        kept, used, dropped = fit_blocks_to_budget(blocks, DEFAULT_CONTEXT_BUDGET)
        assert len(kept) == 10
        assert dropped == 0
