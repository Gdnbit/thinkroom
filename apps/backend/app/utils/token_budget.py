"""Token budget helpers for context assembly (B1).

当 LLM prompt 里的上下文块数量不受约束时，极端情况可能撑爆模型
context window 导致 400 错误。本模块提供一个统一的"贪心截断"预算器，
被 chat_harness / fusion_skill 等路径调用。

启发式：CJK 字符 ≈ 1 token，非 CJK 按 ~4 char / token。
这个估算对 GPT-4o / Claude / Gemini / DeepSeek 等 BPE 类 tokenizer
都是**偏保守**的（会略高估），用作预算上限是安全的。
"""

from __future__ import annotations

from typing import List, Tuple

# 默认给 retrieval context 留 4k token；
# 典型 16k window 里预留 2k system + 1k history + 1k reply + safety margin。
DEFAULT_CONTEXT_BUDGET = 4000

# 极端降级场景（小窗口模型如 32k 老版），可通过 env 或调用方覆盖
MIN_CONTEXT_BUDGET = 1500


def estimate_tokens(text: str) -> int:
    """快速、跨 tokenizer 的保守 token 估算。

    规则：
    - CJK 字符（含中文、日文假名、CJK 符号）按 1 token
    - 其他字符按 4 char / token 向上取整
    - 最少返回 1（避免空串除零）

    Returns:
        int token estimate，约偏高 10%（安全边际）
    """
    if not text:
        return 0
    cjk = 0
    for c in text:
        code = ord(c)
        # CJK Unified Ideographs + CJK Symbols + Hiragana / Katakana
        if 0x4E00 <= code <= 0x9FFF or 0x3000 <= code <= 0x30FF:
            cjk += 1
    other = len(text) - cjk
    return cjk + (other + 3) // 4 + 1


def fit_blocks_to_budget(
    blocks: List[str],
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET,
    *,
    keep_first: int = 1,
) -> Tuple[List[str], int, int]:
    """贪心截断：按顺序保留 blocks 直到预算耗尽。

    使用方应当**先按评分降序排列** blocks，保证高分块一定进入。

    Args:
        blocks: 候选文本段（按重要性降序）
        budget_tokens: 硬上限（单位 token）
        keep_first: 强制保留前 N 个（防止预算过低导致空返回）

    Returns:
        (kept_blocks, used_tokens, dropped_count)
    """
    if budget_tokens <= 0:
        # 异常输入：至少保一条
        if not blocks:
            return [], 0, 0
        return [blocks[0]], estimate_tokens(blocks[0]), len(blocks) - 1

    kept: List[str] = []
    used = 0
    for i, b in enumerate(blocks):
        t = estimate_tokens(b)
        # 前 keep_first 条无条件进入；其后必须能装下
        if i < keep_first:
            kept.append(b)
            used += t
            continue
        if used + t <= budget_tokens:
            kept.append(b)
            used += t
        else:
            # 一旦超限即停止（保持"高分优先"语义）
            break
    return kept, used, len(blocks) - len(kept)


__all__ = [
    "DEFAULT_CONTEXT_BUDGET",
    "MIN_CONTEXT_BUDGET",
    "estimate_tokens",
    "fit_blocks_to_budget",
]
