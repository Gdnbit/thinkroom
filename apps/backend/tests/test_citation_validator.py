"""S2 验证：citation_validator 正确剔除无效 [N] 引用"""
from __future__ import annotations

import pytest
from app.utils.citation_validator import validate_citations, extract_citation_indices


class TestValidateCitations:
    def test_all_valid_citations_pass_through(self):
        text = "数据[1]表明 BTC 走强，但 [2] 提示风险。"
        cleaned, invalid = validate_citations(text, citation_count=2)
        assert cleaned == text
        assert invalid == []

    def test_invalid_citation_removed(self):
        text = "数据[3]显示异常"
        cleaned, invalid = validate_citations(text, citation_count=2)
        assert cleaned == "数据显示异常"
        assert invalid == [3]

    def test_mixed_valid_and_invalid(self):
        text = "[1] 支持 [5] 反对 [2] 条件"
        cleaned, invalid = validate_citations(text, citation_count=3)
        assert "[1]" in cleaned and "[2]" in cleaned
        assert "[5]" not in cleaned
        assert invalid == [5]

    def test_zero_citations_kills_all(self):
        text = "观点[1]和[2]"
        cleaned, invalid = validate_citations(text, citation_count=0)
        assert cleaned == "观点和"
        assert invalid == [1, 2]

    def test_duplicate_invalid_dedup(self):
        text = "[9] 是编造的 [9] 多次出现"
        cleaned, invalid = validate_citations(text, citation_count=3)
        assert invalid == [9]  # 去重

    def test_zero_index_is_invalid(self):
        text = "[0] 不应该出现"
        cleaned, invalid = validate_citations(text, citation_count=3)
        assert cleaned == " 不应该出现"
        assert invalid == [0]

    def test_empty_text(self):
        cleaned, invalid = validate_citations("", citation_count=5)
        assert cleaned == ""
        assert invalid == []

    def test_no_citations_in_text(self):
        text = "纯文本没有任何编号"
        cleaned, invalid = validate_citations(text, citation_count=3)
        assert cleaned == text
        assert invalid == []

    def test_adjacent_citations(self):
        text = "多条支撑[1][2][3]结论"
        cleaned, invalid = validate_citations(text, citation_count=3)
        assert cleaned == text
        assert invalid == []

    def test_adjacent_with_invalid(self):
        text = "[1][2][4]"
        cleaned, invalid = validate_citations(text, citation_count=3)
        assert cleaned == "[1][2]"
        assert invalid == [4]


class TestExtractCitationIndices:
    def test_extract_dedup_sorted(self):
        text = "[3][1][2][1][3]"
        assert extract_citation_indices(text) == [1, 2, 3]

    def test_empty(self):
        assert extract_citation_indices("") == []

    def test_no_citations(self):
        assert extract_citation_indices("普通文本") == []
