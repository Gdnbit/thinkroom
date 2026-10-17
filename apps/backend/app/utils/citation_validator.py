"""Citation Validator — 引用真实性校验

职责：扫描 LLM 输出中的 [N] 引用编号，剔除超出 citations 范围的无效引用。

动机：LLM 可能输出 "[3]" 但 citations 实际只有 [1][2]。前端如不校验会把
无效引用原样展示，产生"看似有据实则编造"的幻觉。

用法：
    cleaned, invalid = validate_citations(reply_text, citation_count=len(citations))
    if invalid:
        logger.warning(f"LLM hallucinated {len(invalid)} invalid citations: {invalid}")
"""
from __future__ import annotations

import re
from typing import List, Tuple


_CITATION_PATTERN = re.compile(r'\[(\d+)\]')


def validate_citations(text: str, citation_count: int) -> Tuple[str, List[int]]:
    """扫描 text 中所有 [N]，剔除超出 citation_count 的编号。

    Args:
        text: LLM 生成的回答文本
        citation_count: 本次调用中实际提供的 citations 条数（1-based）

    Returns:
        (cleaned_text, invalid_indices)
        - cleaned_text：剔除无效 [N] 后的文本
        - invalid_indices：被剔除的编号列表（去重后）

    特殊情况：
        - citation_count == 0：所有 [N] 都视为无效（无证据却引用）
        - N < 1：视为无效
        - 文本中同一 [N] 出现多次：在 invalid_indices 中只记一次
    """
    if not text:
        return text, []

    invalid_set: set[int] = set()

    def _replace(m: re.Match) -> str:
        try:
            n = int(m.group(1))
        except (ValueError, TypeError):
            return m.group(0)
        if n < 1 or n > citation_count:
            invalid_set.add(n)
            return ""  # 剔除无效引用
        return m.group(0)

    cleaned = _CITATION_PATTERN.sub(_replace, text)
    # 返回排序后的无效编号列表
    return cleaned, sorted(invalid_set)


def extract_citation_indices(text: str) -> List[int]:
    """仅提取文本中出现的所有 [N] 编号（去重 + 排序），不做合法性判断。

    辅助诊断用 —— 审计页可统计"LLM 平均每次回答引用了多少 [N]"。
    """
    if not text:
        return []
    nums: set[int] = set()
    for m in _CITATION_PATTERN.finditer(text):
        try:
            nums.add(int(m.group(1)))
        except (ValueError, TypeError):
            pass
    return sorted(nums)


__all__ = ["validate_citations", "extract_citation_indices"]
