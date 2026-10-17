"""A1 验证：asset_alias 识别与规范化"""
from __future__ import annotations

import pytest
from app.utils.asset_alias import detect_asset, detect_assets, canonicalize, ASSET_ALIASES


class TestDetectAsset:
    @pytest.mark.parametrize("text,expected", [
        ("BTC 下周怎么看", "BTC"),
        ("比特币会破前高吗", "BTC"),
        ("bitcoin price prediction", "BTC"),
        ("以太坊 vs 比特币", "ETH"),  # 先命中 ETH（长关键词优先）
        ("gold vs silver", "GOLD"),
        ("黄金还能涨吗", "GOLD"),
        ("WTI 原油价格", "OIL"),
        ("石油多头还能持有吗", "OIL"),
        ("纳斯达克收跌", "NDX"),
        ("标普 500 突破新高", "SPX"),
        ("美元指数 DXY", "DXY"),
        ("VIX 恐慌指数飙升", "VIX"),
    ])
    def test_detect_various_assets(self, text, expected):
        assert detect_asset(text) == expected

    def test_no_match_returns_default(self):
        assert detect_asset("今天天气不错") == "BTC"
        assert detect_asset("random text", default="SPX") == "SPX"

    def test_empty_returns_default(self):
        assert detect_asset(None) == "BTC"
        assert detect_asset("") == "BTC"


class TestDetectAssets:
    def test_multiple_assets_in_order(self):
        # 注意：按关键词出现顺序不保证；我们验证结果集即可
        result = detect_assets("比较 BTC ETH 和 GOLD")
        assert set(result) == {"BTC", "ETH", "GOLD"}

    def test_dedup(self):
        # bitcoin + BTC + 比特币 都命中 BTC，但去重
        result = detect_assets("bitcoin BTC 比特币")
        assert result == ["BTC"]

    def test_ethereum_not_misparsed_as_eth_substring_issue(self):
        """ethereum 这个词会同时命中 eth 和 ethereum；长关键词优先确保得到 ETH 一次"""
        result = detect_assets("ethereum is great")
        assert result == ["ETH"]


class TestCanonicalize:
    @pytest.mark.parametrize("input,expected", [
        ("比特币", "BTC"),
        ("bitcoin", "BTC"),
        ("BTC", "BTC"),
        ("btc", "BTC"),
        ("黄金", "GOLD"),
        ("gold", "GOLD"),
        ("ETH", "ETH"),
        ("UNKNOWN", "UNKNOWN"),  # 未收录 → 大写返回
        ("oil", "OIL"),
    ])
    def test_canonicalize_aliases(self, input, expected):
        assert canonicalize(input) == expected

    def test_canonicalize_none_empty(self):
        assert canonicalize(None) is None
        assert canonicalize("") is None
        assert canonicalize("  ") is None


class TestCanonicalCoverage:
    def test_all_aliases_map_to_uppercase_symbols(self):
        """所有别名映射的 value 都应是全大写规范符号"""
        for alias, sym in ASSET_ALIASES.items():
            assert sym == sym.upper(), f"{alias} -> {sym} 不是规范符号"
            assert sym.isalpha() or sym in ("S&P",), f"{sym} 格式异常"
