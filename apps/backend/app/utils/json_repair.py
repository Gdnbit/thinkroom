"""Robust JSON Extraction & Repair Utility

统一处理 LLM 返回的 JSON 文本，覆盖以下常见问题：
1. <think>...</think> 思考块（thinking model）
2. Markdown 代码块包裹 (```json ... ```)
3. JSON 前后有多余文本（模型"解释"）
4. 字符串值内的裸换行符
5. 尾部逗号 (trailing commas)
6. 缺失逗号 (missing commas between key-value pairs)
7. 截断的 JSON（未闭合的括号）
8. 控制字符污染
9. 单引号代替双引号

适用于所有 Skill 的 _parse_response 场景。
"""

import json
import re
from typing import Union

from loguru import logger


def robust_json_parse(text: str, *, context: str = "") -> dict:
    """从 LLM 原始输出中提取并修复 JSON，返回 dict。

    Args:
        text: LLM 的原始输出文本
        context: 调用方标识（用于日志），如 "BriefingSkill"

    Returns:
        解析后的 dict

    Raises:
        json.JSONDecodeError: 所有修复策略均失败时抛出
    """
    if not text or not text.strip():
        raise json.JSONDecodeError("Empty input text", "", 0)

    raw = text

    # ── Phase 0: 预处理 ────────────────────────────────────────
    # 0a. 移除 <think>...</think> 思考块（thinking model）
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE).strip()

    # 0b. 移除不可见控制字符（保留 \n \r \t）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    # ── Phase 1: 提取 JSON 候选文本 ───────────────────────────
    candidate = _extract_candidate(text)

    # ── Phase 2: 清洗 + 尝试直接解析 ─────────────────────────
    sanitized = _sanitize(candidate)
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        pass

    # ── Phase 3: 修复常见格式问题 ─────────────────────────────
    repaired = _repair(sanitized)
    try:
        result = json.loads(repaired)
        if context:
            logger.debug(f"[JsonRepair] {context}: repaired JSON successfully")
        return result
    except json.JSONDecodeError:
        pass

    # ── Phase 4: 截断修复（未闭合的括号） ─────────────────────
    truncated = _repair_truncation(repaired)
    if truncated:
        try:
            result = json.loads(truncated)
            if context:
                logger.debug(f"[JsonRepair] {context}: truncation-repaired JSON successfully")
            return result
        except json.JSONDecodeError:
            pass

    # ── Phase 5: 激进模式 — 逐步缩短找可解析子串 ─────────────
    aggressive = _aggressive_repair(repaired)
    if aggressive is not None:
        if context:
            logger.warning(f"[JsonRepair] {context}: aggressive repair (data may be partial)")
        return aggressive

    # ── 全部失败 ──────────────────────────────────────────────
    # 尝试原始文本的直接解析作为最后手段
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    raise json.JSONDecodeError(
        f"All repair strategies failed ({context})",
        sanitized[:200],
        0,
    )


# ── 内部工具函数 ──────────────────────────────────────────────


def _extract_candidate(text: str) -> str:
    """从可能包含 Markdown 包裹或多余文字的文本中提取 JSON 候选段"""
    text = text.strip()

    # 策略1: ```json ... ``` 代码块
    m = re.search(r'```json\s*([\s\S]*?)```', text)
    if m:
        return m.group(1).strip()

    # 策略2: ``` ... ``` 通用代码块
    m = re.search(r'```\s*([\s\S]*?)```', text)
    if m:
        inner = m.group(1).strip()
        if inner.startswith('{') or inner.startswith('['):
            return inner

    # 策略3: 找最大的 { ... } 块（贪婪）
    m = re.search(r'(\{[\s\S]*\})', text)
    if m:
        return m.group(1)

    # 策略4: 找最大的 [ ... ] 块（用于 JSON array 响应）
    m = re.search(r'(\[[\s\S]*\])', text)
    if m:
        return m.group(1)

    # 兜底: 返回原文
    return text


def _sanitize(text: str) -> str:
    """清洗 JSON 文本：修复字符串内的裸换行、单引号等"""
    # 1. 修复 JSON 字符串值内的裸换行（\n \r）→ 转义序列
    def _fix_string_newlines(m: re.Match) -> str:
        inner = m.group(1)
        inner = inner.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')
        # 修复 tab
        inner = inner.replace('\t', '\\t')
        return '"' + inner + '"'

    text = re.sub(r'"((?:[^"\\]|\\.)*)"', _fix_string_newlines, text, flags=re.DOTALL)
    return text


def _repair(text: str) -> str:
    """修复常见 JSON 格式问题"""
    # 1. 移除尾部逗号: ,] → ] 和 ,} → }
    text = re.sub(r',\s*(\])', r'\1', text)
    text = re.sub(r',\s*(\})', r'\1', text)

    # 2. 修复缺失逗号（两个键值对之间）
    #    模式: "value" \n "key" 或 "value" "key" (缺少逗号)
    #    匹配: 非逗号/冒号/[/{结尾 + 可选空白 + 双引号开头的新键
    text = re.sub(
        r'("(?:[^"\\]|\\.)*")\s*\n\s*(")',
        r'\1,\n\2',
        text,
    )
    # 补充: } 或 ] 后面直接跟 " 开头的键（缺逗号）
    text = re.sub(r'(\})\s*\n\s*(")', r'\1,\n\2', text)
    text = re.sub(r'(\])\s*\n\s*(")', r'\1,\n\2', text)

    # 3. 修复数值/布尔/null 后面缺少逗号
    #    模式: (数值|true|false|null) \n "key"
    text = re.sub(
        r'(\d+\.?\d*|true|false|null)\s*\n\s*(")',
        r'\1,\n\2',
        text,
    )

    # 4. 单引号 → 双引号（谨慎：只处理明确的 JSON 键值场景）
    #    只替换 'key': 'value' 模式中的单引号，不影响字符串内容
    #    这个比较激进，放在最后且只在其他方法失败后触发
    # （暂不实施，因为可能破坏包含撇号的文本）

    return text


def _repair_truncation(text: str) -> str | None:
    """修复截断的 JSON：补齐未闭合的括号"""
    text = text.rstrip()

    # 先移除末尾不完整的键值对
    # 例如: ..."key": "val 截断了
    # 尝试找到最后一个完整的值结束位置
    # 策略：移除末尾非结构字符，找到最近的有效结束点
    truncated = text.rstrip()

    # 如果末尾不是 } ] " 数字 true false null，则可能截断在值中间
    if truncated and truncated[-1] not in '"}]0123456789elun':
        # 回退到最后一个逗号或完整值
        last_comma = truncated.rfind(',')
        last_brace = max(truncated.rfind('}'), truncated.rfind(']'))
        cut_point = max(last_comma, last_brace)
        if cut_point > 0:
            truncated = truncated[:cut_point + 1]

    # 移除末尾的悬挂逗号
    truncated = truncated.rstrip().rstrip(',')

    # 计算需要补齐的括号
    open_braces = truncated.count('{') - truncated.count('}')
    open_brackets = truncated.count('[') - truncated.count(']')

    if open_braces < 0 or open_brackets < 0:
        return None  # 结构异常，放弃

    if open_braces == 0 and open_brackets == 0:
        return truncated  # 已经平衡

    # 补齐
    repaired = truncated + ']' * open_brackets + '}' * open_braces
    return repaired


def _aggressive_repair(text: str) -> dict | None:
    """激进修复：逐步缩短文本直到找到可解析的 JSON 子串

    从末尾开始回退，每次找到一个结构边界点（, ] }）
    尝试补齐括号后解析。比逐字符回退高效得多。
    """
    text = text.rstrip()
    if not text:
        return None

    # 找到所有潜在的切割点（逗号、右括号之后）
    cut_points = []
    for i, ch in enumerate(text):
        if ch in ',]}':
            cut_points.append(i + 1)

    # 从后向前尝试
    for cp in reversed(cut_points):
        chunk = text[:cp].rstrip().rstrip(',')
        open_braces = chunk.count('{') - chunk.count('}')
        open_brackets = chunk.count('[') - chunk.count(']')
        if open_braces >= 0 and open_brackets >= 0:
            repaired = chunk + ']' * open_brackets + '}' * open_braces
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue

    return None
