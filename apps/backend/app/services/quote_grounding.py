"""
Quote Grounding — 将 LLM 输出的 full_quote 回溯到原文中精确匹配的片段。

设计参考:
  - Google LangExtract (2025): LLM 语义提取 + difflib.SequenceMatcher 精确定位
  - LLMQuoter (arXiv 2501.05554): quote-first-then-answer 策略

流程:
  1. exact match: 直接在原文中查找
  2. normalized match: 去除空白/标点差异后匹配
  3. fuzzy match: 滑窗 + SequenceMatcher，找到 ≥ threshold 的最佳片段
  4. 命中时用原文片段替换 LLM 输出，保证 verbatim
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MatchStatus(Enum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    FUZZY = "fuzzy"
    NOT_FOUND = "not_found"


@dataclass
class GroundingResult:
    """引用回溯结果"""
    original_quote: str        # LLM 输出的原始 quote
    grounded_quote: str        # 回溯后的原文片段（如未匹配则保持原样）
    status: MatchStatus        # 匹配状态
    score: float               # 相似度分数 (0.0-1.0)
    start: Optional[int]       # 在原文中的起始位置
    end: Optional[int]         # 在原文中的结束位置

    @property
    def is_grounded(self) -> bool:
        return self.status in (MatchStatus.EXACT, MatchStatus.NORMALIZED, MatchStatus.FUZZY)


# ── 文本标准化 ──

def _normalize(text: str) -> str:
    """标准化文本：Unicode NFKC + 合并空白 + 去除首尾空白"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compact(text: str) -> str:
    """紧凑化：去除所有空白和标点，只保留文字内容（适合中文匹配）"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    # 保留中文字符、字母、数字
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text)


# ── 精确匹配 ──

def _exact_match(source: str, quote: str) -> Optional[GroundingResult]:
    """在原文中直接查找 quote"""
    idx = source.find(quote)
    if idx != -1:
        return GroundingResult(
            original_quote=quote,
            grounded_quote=source[idx:idx + len(quote)],
            status=MatchStatus.EXACT,
            score=1.0,
            start=idx,
            end=idx + len(quote),
        )
    return None


def _normalized_match(source: str, quote: str) -> Optional[GroundingResult]:
    """标准化后匹配（处理空白/标点差异）"""
    # 策略 A: 合并空白后匹配
    norm_src = _normalize(source)
    norm_q = _normalize(quote)
    if not norm_q:
        return None

    idx = norm_src.find(norm_q)
    if idx != -1:
        grounded = norm_src[idx:idx + len(norm_q)]
        orig_start = _find_original_span(source, grounded)
        if orig_start is not None:
            end = min(orig_start + len(grounded) + 50, len(source))
            orig_span = _trim_to_normalized_length(source[orig_start:end], len(norm_q))
            return GroundingResult(
                original_quote=quote,
                grounded_quote=orig_span,
                status=MatchStatus.NORMALIZED,
                score=0.98,
                start=orig_start,
                end=orig_start + len(orig_span),
            )

    # 策略 B: 紧凑化匹配（去除所有空白和标点，适合中文）
    compact_src = _compact(source)
    compact_q = _compact(quote)
    if compact_q and compact_q in compact_src:
        cidx = compact_src.find(compact_q)
        # 回映射到原文: 紧凑字符的第 cidx 个对应原文什么位置
        orig_start = _map_compact_to_original(source, cidx)
        orig_end = _map_compact_to_original(source, cidx + len(compact_q))
        if orig_start is not None and orig_end is not None:
            orig_span = source[orig_start:orig_end]
            return GroundingResult(
                original_quote=quote,
                grounded_quote=orig_span,
                status=MatchStatus.NORMALIZED,
                score=0.97,
                start=orig_start,
                end=orig_end,
            )
    return None


def _find_original_span(source: str, normalized_snippet: str) -> Optional[int]:
    """在原文中找到 normalized snippet 对应的起始位置"""
    # 取 snippet 的前 15 个字符作为锚点
    anchor = normalized_snippet[:15].strip()
    if not anchor:
        return None
    # 在原文中搜索（忽略空白差异）
    pattern = re.sub(r"\s+", r"\\s*", re.escape(anchor))
    m = re.search(pattern, source)
    return m.start() if m else None


def _map_compact_to_original(source: str, compact_idx: int) -> Optional[int]:
    """将 compact 字符串中的索引映射回原文索引"""
    count = 0
    for i, ch in enumerate(source):
        # 跳过非文字字符（与 _compact 逻辑一致）
        if re.match(r"[\w\u4e00-\u9fff]", ch):
            if count == compact_idx:
                return i
            count += 1
    # 如果到达末尾，返回原文长度
    return len(source) if count == compact_idx else None


def _trim_to_normalized_length(text: str, target_norm_len: int) -> str:
    """从原文截取，直到 normalized 后长度达到目标"""
    result = []
    norm_len = 0
    in_space = False
    for ch in text:
        result.append(ch)
        if ch.isspace():
            if not in_space:
                norm_len += 1
                in_space = True
        else:
            norm_len += 1
            in_space = False
        if norm_len >= target_norm_len:
            break
    return "".join(result).rstrip()


# ── 模糊匹配 ──

def _fuzzy_match(
    source: str,
    quote: str,
    threshold: float = 0.65,
) -> Optional[GroundingResult]:
    """滑窗 + SequenceMatcher 模糊匹配（在 compact 空间搜索，对中文友好）"""
    compact_src = _compact(source)
    compact_q = _compact(quote)

    if not compact_q or not compact_src:
        return GroundingResult(
            original_quote=quote, grounded_quote=quote,
            status=MatchStatus.NOT_FOUND, score=0.0, start=None, end=None,
        )

    q_len = len(compact_q)
    # padding 与 query 长度成正比，短文本用小窗口
    padding = max(10, q_len // 3)
    window_size = q_len + padding
    step = max(2, q_len // 6)

    best_score = 0.0
    best_start = 0

    for i in range(0, max(1, len(compact_src) - q_len + 1), step):
        window = compact_src[i:i + window_size]
        matcher = difflib.SequenceMatcher(None, compact_q, window, autojunk=False)
        score = matcher.ratio()
        if score > best_score:
            best_score = score
            best_start = i

    if best_score < threshold:
        return GroundingResult(
            original_quote=quote, grounded_quote=quote,
            status=MatchStatus.NOT_FOUND, score=best_score, start=None, end=None,
        )

    # 从 compact 空间回映射到原文
    # 精炼匹配范围：在最佳位置附近做更细粒度搜索
    fine_start = max(0, best_start - step)
    fine_end = min(len(compact_src), best_start + q_len + step + padding)
    fine_best_score = 0.0
    fine_best_pos = best_start

    for i in range(fine_start, min(fine_end, len(compact_src) - q_len + 1)):
        window = compact_src[i:i + q_len]
        matcher = difflib.SequenceMatcher(None, compact_q, window, autojunk=False)
        score = matcher.ratio()
        if score > fine_best_score:
            fine_best_score = score
            fine_best_pos = i

    # 回映射到原文坐标
    orig_start = _map_compact_to_original(source, fine_best_pos)
    orig_end = _map_compact_to_original(source, fine_best_pos + q_len)

    if orig_start is not None and orig_end is not None:
        orig_span = source[orig_start:orig_end]
        return GroundingResult(
            original_quote=quote,
            grounded_quote=orig_span,
            status=MatchStatus.FUZZY,
            score=fine_best_score,
            start=orig_start,
            end=orig_end,
        )

    return GroundingResult(
        original_quote=quote, grounded_quote=quote,
        status=MatchStatus.NOT_FOUND, score=best_score, start=None, end=None,
    )


# ── 主入口 ──

def ground_quote(
    source_text: str,
    llm_quote: str,
    threshold: float = 0.75,
) -> GroundingResult:
    """
    将 LLM 输出的 full_quote 回溯到原文中精确匹配的片段。

    优先级: exact → normalized → fuzzy → not_found

    Args:
        source_text: 原始文本（raw_text）
        llm_quote: LLM 输出的 full_quote
        threshold: fuzzy match 最低相似度阈值

    Returns:
        GroundingResult，grounded_quote 为回溯后的原文片段
    """
    if not llm_quote or not source_text:
        return GroundingResult(
            original_quote=llm_quote or "",
            grounded_quote=llm_quote or "",
            status=MatchStatus.NOT_FOUND,
            score=0.0,
            start=None,
            end=None,
        )

    # Step 1: Exact match
    result = _exact_match(source_text, llm_quote)
    if result:
        return result

    # Step 2: Normalized match (whitespace/punct tolerance)
    result = _normalized_match(source_text, llm_quote)
    if result:
        return result

    # Step 3: Fuzzy match (sliding window)
    return _fuzzy_match(source_text, llm_quote, threshold=threshold)
