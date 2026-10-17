"""B4 · citation 语义方向校验测试"""
from dataclasses import dataclass
from typing import Optional

import pytest

from app.utils.citation_stance_checker import (
    check_citation_stance_alignment,
    CitationStanceMismatch,
)


@dataclass
class _Cite:
    index: int
    stance: str
    claim_id: str = ""
    summary: str = ""


class TestBasicMismatch:
    def test_bullish_text_cites_bearish_claim(self):
        citations = [_Cite(index=1, stance="bearish")]
        text = "BTC 有望突破 70k 新高 [1]。"
        m = check_citation_stance_alignment(text, citations)
        assert len(m) == 1
        assert m[0].index == 1
        assert m[0].claim_stance == "bearish"
        assert m[0].text_direction == "bullish"

    def test_bearish_text_cites_bullish_claim(self):
        citations = [_Cite(index=1, stance="bullish")]
        text = "BTC 跌破关键支撑，承压严重 [1]。"
        m = check_citation_stance_alignment(text, citations)
        assert len(m) == 1
        assert m[0].text_direction == "bearish"
        assert m[0].claim_stance == "bullish"


class TestNoMismatch:
    def test_aligned_bullish(self):
        citations = [_Cite(index=1, stance="bullish")]
        text = "BTC 有望上涨突破新高 [1]。"
        assert check_citation_stance_alignment(text, citations) == []

    def test_aligned_bearish(self):
        citations = [_Cite(index=1, stance="bearish")]
        text = "BTC 面临下跌风险 [1]。"
        assert check_citation_stance_alignment(text, citations) == []

    def test_neutral_claim_skipped(self):
        # neutral 立场的 claim 无所谓方向 → 不告警
        citations = [_Cite(index=1, stance="neutral")]
        text = "BTC 会大幅上涨 [1]。"
        assert check_citation_stance_alignment(text, citations) == []

    def test_conditional_claim_skipped_by_default(self):
        citations = [_Cite(index=1, stance="conditional")]
        text = "BTC 势必下跌崩盘 [1]。"
        assert check_citation_stance_alignment(text, citations) == []

    def test_neutral_sentence_not_flagged(self):
        # 句子无明显方向关键词 → 不告警（避免假警报）
        citations = [_Cite(index=1, stance="bullish")]
        text = "关于 BTC 的观点见 [1]。"
        assert check_citation_stance_alignment(text, citations) == []


class TestNegation:
    def test_negation_flips_direction(self):
        # "不看涨" → 实际 bearish 倾向，不应被判为 bullish
        citations = [_Cite(index=1, stance="bearish")]
        text = "短期内并不看涨 [1]。"
        # 句子方向：带否定的 "看涨" → 反转；若 bear 关键词不出现，仍可能 neutral
        # 只验证不会把它判为 bullish-vs-bearish mismatch
        result = check_citation_stance_alignment(text, citations)
        assert all(m.text_direction != "bullish" for m in result)


class TestInvalidIndex:
    def test_out_of_range_ignored(self):
        citations = [_Cite(index=1, stance="bullish")]
        text = "BTC 跌破支撑 [2]。"  # [2] 越界
        # 越界引用交给 citation_validator 处理，这里不告警
        assert check_citation_stance_alignment(text, citations) == []


class TestDictCitation:
    def test_dict_payload_works(self):
        # citations 也可以是 dict（chat.py 里会传 citation objects，但这里验证 dict 兼容）
        citations = [{"index": 1, "stance": "bullish", "claim_id": "x"}]
        text = "BTC 已跌破关键支撑 [1]。"
        m = check_citation_stance_alignment(text, citations)
        assert len(m) == 1
        assert m[0].claim_stance == "bullish"
        assert m[0].text_direction == "bearish"


class TestMultipleCitations:
    def test_multiple_mismatches(self):
        citations = [
            _Cite(index=1, stance="bullish"),
            _Cite(index=2, stance="bearish"),
        ]
        text = "BTC 跌破支撑 [1]。ETH 有望强势突破新高 [2]。"
        m = check_citation_stance_alignment(text, citations)
        assert len(m) == 2
        indexes = [x.index for x in m]
        assert 1 in indexes and 2 in indexes

    def test_mixed_ok_and_mismatch(self):
        citations = [
            _Cite(index=1, stance="bullish"),   # 正确引用
            _Cite(index=2, stance="bearish"),   # 误用
        ]
        text = "BTC 有望上涨 [1]。BTC 即将大涨到 70k [2]。"
        m = check_citation_stance_alignment(text, citations)
        assert len(m) == 1
        assert m[0].index == 2

    def test_opposite_facets_in_one_sentence_use_local_citation_clauses(self):
        citations = [
            _Cite(index=1, stance="bearish"),
            _Cite(index=2, stance="bullish"),
        ]
        text = (
            "中国区市场呈现供应收缩与需求增长并存："
            "样本库存同比下降 8% [1]，而样本订单同比增长 12% [2]。"
        )

        assert check_citation_stance_alignment(text, citations) == []


class TestEmptyInput:
    def test_empty_text(self):
        assert check_citation_stance_alignment("", [_Cite(1, "bullish")]) == []

    def test_empty_citations(self):
        assert check_citation_stance_alignment("BTC 上涨 [1]", []) == []

    def test_no_citations_in_text(self):
        assert check_citation_stance_alignment("BTC 上涨突破新高", [_Cite(1, "bullish")]) == []
