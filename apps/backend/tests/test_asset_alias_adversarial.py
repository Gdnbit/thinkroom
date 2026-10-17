"""B5 · asset_alias 对抗用例：验证词边界检查防止误识别

以前 "how to solve this" 会被识别为 SOL，"method of analysis" 会被识别为 ETH。
本测试固化这些 bug 不会回归。
"""
import pytest

from app.utils.asset_alias import detect_asset, detect_assets


class TestNegatives:
    """应当不识别任何资产的场景（纯负例）"""

    @pytest.mark.parametrize("text,reason", [
        ("how to solve this problem", "solve ≠ SOL"),
        ("solve the equation", "solve ≠ SOL"),
        ("solution is unclear", "solution ≠ SOL"),
        ("solid foundation", "solid ≠ SOL"),
        ("solar power", "solar ≠ SOL"),
        ("method of analysis", "method ≠ ETH"),
        ("ethics of AI", "ethics ≠ ETH"),
        ("aesthetic design", "aesthetic ≠ ETH"),
        ("breath of fresh air", "breath ≠ ETH"),
        ("ether network", "ether ≠ ETH (eth alias 不含 'ether')"),
        ("did not spoil today", "spoil ≠ OIL"),
        ("recoil from impact", "recoil ≠ OIL"),
        ("turmoil ahead", "turmoil ≠ OIL"),
        ("boil the water", "boil ≠ OIL"),
        ("going below support", "below ≠ DOW/DJI"),
        ("slow down the trade", "slowdown ≠ DJI"),
        ("tomorrow is the day", "tomorrow ≠ DJI"),
        ("shutdown of trading", "shutdown ≠ DJI"),
        ("fixed income strategy", "income ≠ anything"),
        ("arbitrage strategy discussion", "arb/arbitrage 未收录且 ARB 非 alias"),
        ("random text without any asset", "完全无关"),
    ])
    def test_no_false_positive(self, text: str, reason: str):
        result = detect_assets(text)
        assert result == [], f"期望空，但识别到 {result}（{reason}）"


class TestPositives:
    """应当识别的场景（已有功能回归保障）"""

    @pytest.mark.parametrize("text,expected", [
        ("BTC 突破 75000", "BTC"),
        ("btc 走势", "BTC"),
        ("比特币再创新高", "BTC"),
        ("ETH 还会继续涨吗", "ETH"),
        ("以太坊合并后如何", "ETH"),
        ("SOL 最近如何", "SOL"),
        ("Solana 生态", "SOL"),
        ("XRP 法律进展", "XRP"),
        ("黄金避险", "GOLD"),
        ("gold price", "GOLD"),
        ("标普 500 收盘", "SPX"),
        ("纳指调整", "NDX"),
        ("美元指数走强", "DXY"),
        ("恐慌指数飙升", "VIX"),
    ])
    def test_first_match(self, text: str, expected: str):
        assert detect_asset(text, default="NOMATCH") == expected


class TestEdgeCases:
    def test_with_punctuation(self):
        # 标点包围：BTC. 仍应识别
        assert "BTC" in detect_assets("Today, BTC. was volatile")

    def test_with_numbers(self):
        # BTC-USDT：数字/符号边界外 → 识别
        assert "BTC" in detect_assets("trade BTC-USDT pair")
        assert "ETH" in detect_assets("ETH/BTC ratio is key")

    def test_embedded_in_word_rejected(self):
        # methodeth 这种奇葩拼接不应识别（无词边界）
        assert detect_assets("abcmethoxyz") == []
        # 但 "BTC" 作为独立词应被识别
        assert detect_assets("btcxyz") == []  # btcxyz 无边界，应该拒绝

    def test_multiple_assets(self):
        assert detect_assets("ETH 和 BTC 比较") == ["ETH", "BTC"]

    def test_case_insensitive(self):
        # 大小写不敏感
        assert detect_asset("BITCOIN price surge", default="X") == "BTC"

    def test_cjk_substring_still_works(self):
        # CJK 别名保持子串匹配（如"比特币上涨"中"比特币"应命中）
        assert detect_asset("比特币上涨了", default="X") == "BTC"

    def test_mixed_cjk_en(self):
        # 中英混合
        result = detect_assets("BTC vs 以太坊")
        assert "BTC" in result
        assert "ETH" in result

    def test_empty(self):
        assert detect_assets("") == []
        assert detect_assets(None) == []
