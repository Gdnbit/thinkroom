"""B4 · Citation 语义方向校验

问题：`citation_validator.validate_citations` 只检查 ``[N]`` 编号是否在
citations 数组的合法范围内（是否存在），不检查被引用 claim 的立场
是否与引用位置的文本情感一致。

典型误用场景：
    context:  [1] 看空 — BTC 下跌至 60k
              [2] 看多 — ETH 技术面良好
    LLM 输出: "BTC 有望突破 70k [1]"
    → [1] 存在（通过旧校验），但语义相反（LLM 以牛市叙事引用了熊市证据）。

本模块提供 **warn-only 检查**（不修改文本）：
    mismatches = check_citation_stance_alignment(reply_text, citations)
    for m in mismatches:
        logger.warning(f"[{m.index}] text=bullish but claim=bearish")

实现基于关键词启发式（中英双语），非 ML；适合轻量诊断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence


# ── 情感关键词词典（按立场分类）────────────────────────────────
_BULLISH_KEYWORDS = {
    # 中文（方向明确的词；"支撑/阻力"为中性交易术语，已排除）
    "看涨", "看多", "上涨", "上行", "利好", "突破", "买入", "增持",
    "增长", "强势", "反弹", "牛市", "回暖", "修复", "新高",
    "看好", "做多", "吸筹", "抄底", "加仓", "走强", "大涨",
    # 英文
    "bullish", "buy", "long", "uptrend", "rally", "breakout",
    "surge", "rebound", "accumulate", "upside",
}

_BEARISH_KEYWORDS = {
    # 中文
    "看跌", "看空", "下跌", "下行", "利空", "跌破", "卖出", "减持",
    "下滑", "弱势", "调整", "熊市", "回调", "承压", "新低",
    "做空", "抛售", "崩盘", "走弱", "离场", "大跌",
    # 英文
    "bearish", "sell", "short", "downtrend", "decline",
    "breakdown", "crash", "correction", "downside",
}

# 否定词（出现在关键词前 N 字内会反转立场）
_NEGATION_MARKERS = {"不", "未", "没", "否", "难以", "并非", "not", "no", "cannot", "don't"}

# 句子切分：中文标点 + 英文标点
_SENTENCE_SPLIT_RE = re.compile(r'[。！？!?；;\n]+')

# Stance belongs to the clause immediately supported by a citation. Using the
# whole sentence causes a valid "inventory down [1], orders up [2]" synthesis
# to make the two directions contaminate each other.
_CLAUSE_SPLIT_RE = re.compile(r'[。！？!?；;\n，,：:]+')

# 匹配 [N] 引用
_CITATION_RE = re.compile(r'\[(\d+)\]')


@dataclass
class CitationStanceMismatch:
    """单条方向不一致记录"""
    index: int                 # [N] 的 N
    claim_stance: str          # bullish | bearish | neutral | conditional | unknown
    text_direction: str        # bullish | bearish | neutral
    sentence: str              # 包含该引用的原句（截断 120 字）


def _normalize_stance(raw: Optional[str]) -> str:
    s = (raw or "").lower()
    if s in ("bullish", "bull", "long", "positive"):
        return "bullish"
    if s in ("bearish", "bear", "short", "negative"):
        return "bearish"
    if s in ("conditional", "conditional_bullish", "conditional_bearish"):
        return "conditional"
    if s in ("neutral", "", None):
        return "neutral"
    return "neutral"


def _detect_text_direction(sentence: str) -> str:
    """粗粒度判断一个句子偏看多还是看空。

    逻辑：
      - 统计 bullish / bearish 关键词出现次数
      - 对关键词前 6 字内的否定词做反转
      - 多的一方胜出；并列则 neutral
    """
    if not sentence:
        return "neutral"
    lower = sentence.lower()

    def _count_with_negation(keywords: Iterable[str]) -> int:
        total = 0
        for kw in keywords:
            start = 0
            while True:
                idx = lower.find(kw, start)
                if idx < 0:
                    break
                # 检查前 6 字符窗口内是否有否定词
                window = lower[max(0, idx - 6):idx]
                negated = any(neg in window for neg in _NEGATION_MARKERS)
                total += -1 if negated else 1
                start = idx + len(kw)
        return total

    bull_score = _count_with_negation(_BULLISH_KEYWORDS)
    bear_score = _count_with_negation(_BEARISH_KEYWORDS)

    if bull_score > bear_score:
        return "bullish"
    if bear_score > bull_score:
        return "bearish"
    return "neutral"


def _find_sentence_for_citation(text: str, citation_pos: int) -> str:
    """定位包含 ``citation_pos`` 的局部分句。"""
    # 从 citation_pos 向左找最近的分句起点
    left = 0
    for m in _CLAUSE_SPLIT_RE.finditer(text[:citation_pos]):
        left = m.end()
    # 向右找最近的分句终点
    right_match = _CLAUSE_SPLIT_RE.search(text, citation_pos)
    right = right_match.start() if right_match else len(text)
    return text[left:right].strip()


def check_citation_stance_alignment(
    text: str,
    citations: Sequence[dict | object],
    *,
    conditional_neutral: bool = True,
) -> List[CitationStanceMismatch]:
    """扫描 ``text`` 中的 ``[N]`` 引用，检查每处文本方向是否与 citations[N-1] 的立场一致。

    Args:
        text: LLM 输出
        citations: 按 index 升序排列的 citations 列表；每项需含 ``stance`` 字段
                   （Citation dataclass、dict、或任何带 ``stance`` 属性的对象均可）
        conditional_neutral: True 时把 ``conditional`` 视为 neutral（不告警）

    Returns:
        mismatches 列表；每项记录一次方向不一致的引用。
    """
    if not text or not citations:
        return []

    # 规范化 citations（支持 dict 或 dataclass）
    def _get_stance(c: dict | object) -> str:
        if isinstance(c, dict):
            return _normalize_stance(c.get("stance"))
        return _normalize_stance(getattr(c, "stance", None))

    citation_stances: List[str] = [_get_stance(c) for c in citations]
    n = len(citation_stances)

    mismatches: List[CitationStanceMismatch] = []
    seen_pairs: set[tuple[int, int]] = set()  # (index, sentence_start) 去重，避免同句多次

    for m in _CITATION_RE.finditer(text):
        try:
            idx = int(m.group(1))
        except ValueError:
            continue
        if idx < 1 or idx > n:
            continue  # 无效编号由 validate_citations 处理

        claim_stance = citation_stances[idx - 1]
        # 立场未知/中性/条件性 → 跳过（无法判断是否冲突）
        if claim_stance not in ("bullish", "bearish"):
            continue
        if claim_stance == "conditional" and conditional_neutral:
            continue

        sentence = _find_sentence_for_citation(text, m.start())
        if not sentence:
            continue

        # 去重：同一 [N] 在同一句内多次引用只报一次
        key = (idx, text.rfind(sentence, 0, m.start() + 1))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        direction = _detect_text_direction(sentence)
        # 只有当文本方向明确且与 claim 立场相反时才告警
        if direction in ("bullish", "bearish") and direction != claim_stance:
            mismatches.append(CitationStanceMismatch(
                index=idx,
                claim_stance=claim_stance,
                text_direction=direction,
                sentence=sentence[:120] + ("…" if len(sentence) > 120 else ""),
            ))

    return mismatches


__all__ = [
    "CitationStanceMismatch",
    "check_citation_stance_alignment",
]
