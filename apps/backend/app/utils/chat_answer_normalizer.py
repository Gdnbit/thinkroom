from __future__ import annotations

import json
import re
from typing import Any


ANSWER_KEYS = (
    "synthesis_narrative",
    "final_answer",
    "answer",
    "summary",
    "executive_summary",
    "conclusion",
)

SECTION_KEYS = (
    ("analysis", "分析依据"),
    ("rationale", "分析依据"),
    ("key_drivers", "关键因素"),
    ("risks", "风险与不确定性"),
    ("next_steps", "下一步"),
)

NESTED_SKIP_KEYS = {
    "debug",
    "trace",
    "llm_trace",
    "metadata",
    "raw",
    "raw_output",
    "quality_report",
    "quality_filters",
}

_REFERENCE_DATE_PATTERNS = (
    re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}"),
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"),
)

_LEADING_REFERENCE_TIME_PATTERN = re.compile(
    r"^\s*(?:截至|参考时间(?:为|是)?[:：]?)\s*"
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
    r"(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\s*"
    r"(?:CST|UTC(?:[+-]\d{1,2}:?\d{2})?|北京时间)?\s*[，,。:：]?\s*",
    re.IGNORECASE,
)

_ABSOLUTE_DATE_COMPONENT_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?"
)


def _cjk_count(value: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", value or ""))


def repair_text_encoding(value: str) -> str:
    """Repair common UTF-8 bytes decoded as latin-1 mojibake."""
    if not value:
        return value
    if not re.search(r"[ÃÂâäåæçèéêëïð]", value):
        return value
    try:
        decoded = value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value
    if _cjk_count(decoded) > _cjk_count(value):
        return decoded
    return value


def _strip_fence(value: str) -> str:
    text = value.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else text


def _parse_json_like(value: str) -> Any | None:
    text = _strip_fence(value)
    if not (text.startswith("{") or text.startswith("[")):
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return repair_text_encoding(value).strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _format_value(item)
            if text:
                parts.append(f"- {text}")
        return "\n".join(parts)
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            text = _format_value(item)
            if text:
                lines.append(f"- {key}: {text}")
        return "\n".join(lines)
    return str(value).strip()


def _find_user_facing_text(value: Any, *, depth: int = 0) -> str:
    if value is None or depth > 4:
        return ""
    if isinstance(value, str):
        repaired = repair_text_encoding(value).strip()
        nested = _parse_json_like(repaired)
        if nested is not None:
            return _find_user_facing_text(nested, depth=depth + 1)
        return repaired
    if isinstance(value, list):
        parts = [
            _find_user_facing_text(item, depth=depth + 1)
            for item in value
        ]
        return "\n".join(f"- {part}" for part in parts if part)
    if not isinstance(value, dict):
        return str(value).strip()

    for key in ANSWER_KEYS:
        candidate = _find_user_facing_text(value.get(key), depth=depth + 1)
        if candidate:
            return candidate

    for key, item in value.items():
        if key in NESTED_SKIP_KEYS:
            continue
        candidate = _find_user_facing_text(item, depth=depth + 1)
        if candidate:
            return candidate
    return ""


def normalize_chat_answer(raw: str) -> str:
    """Return user-facing chat text, never a raw JSON object when parsing succeeds."""
    repaired = repair_text_encoding(raw or "").strip()
    parsed = _parse_json_like(repaired)
    if not isinstance(parsed, dict):
        return repaired

    sections: list[str] = []
    primary = ""
    primary_key = ""
    for key in ANSWER_KEYS:
        candidate = _format_value(parsed.get(key))
        if candidate:
            primary = candidate
            primary_key = key
            break
    if not primary:
        nested_primary = _find_user_facing_text(parsed)
        if nested_primary:
            primary = nested_primary
            primary_key = "__nested__"
    if not primary:
        for key in ("analysis", "rationale"):
            candidate = _format_value(parsed.get(key))
            if candidate:
                primary = candidate
                primary_key = key
                break
    if primary:
        sections.append(primary)

    for key, label in SECTION_KEYS:
        if key == primary_key:
            continue
        text = _format_value(parsed.get(key))
        if text and text != primary:
            sections.append(f"**{label}**\n{text}")

    if not sections:
        sections.append(_format_value(parsed))
    return "\n\n".join(part for part in sections if part).strip() or repaired


def _contains_reference_date(text: str) -> bool:
    if not text:
        return False
    first_block = "\n".join((text or "").strip().splitlines()[:3])
    return any(pattern.search(first_block) for pattern in _REFERENCE_DATE_PATTERNS)


def ensure_answer_reference_time(text: str, *, reference_time: str) -> str:
    """Expose the request's canonical reference time at the start of the answer."""
    cleaned = (text or "").strip()
    if not cleaned or not reference_time:
        return cleaned
    first_block = "\n".join(cleaned.splitlines()[:3])
    if reference_time in first_block:
        return cleaned
    leading_match = _LEADING_REFERENCE_TIME_PATTERN.match(cleaned)
    if leading_match:
        remainder = cleaned[leading_match.end():].lstrip()
        return f"截至 {reference_time}，{remainder}" if remainder else f"截至 {reference_time}。"
    return f"截至 {reference_time}，以下分析基于当前可用资料与系统检索结果。\n\n{cleaned}"


def _normalized_absolute_dates(text: str) -> list[str]:
    dates: list[str] = []
    for match in _ABSOLUTE_DATE_COMPONENT_RE.finditer(text or ""):
        normalized = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        if normalized not in dates:
            dates.append(normalized)
    return dates


def ensure_answer_question_time_context(
    text: str,
    *,
    question: str,
    reference_time: str,
) -> str:
    """Keep user-specified as-of dates distinct from the system generation time."""
    cleaned = ensure_answer_reference_time(text, reference_time=reference_time)
    question_dates = _normalized_absolute_dates(question)
    if not cleaned or not question_dates:
        return cleaned

    answer_dates = set(_normalized_absolute_dates(cleaned))
    missing_dates = [date for date in question_dates if date not in answer_dates]
    if not missing_dates:
        return cleaned

    requested_context = "、".join(missing_dates)
    prefix = f"截至 {reference_time}"
    if cleaned.startswith(prefix):
        return (
            f"{prefix}（系统生成时间；问题参考时间：{requested_context}）"
            f"{cleaned[len(prefix):]}"
        )
    return (
        f"系统生成时间：{reference_time}；问题参考时间：{requested_context}。\n\n"
        f"{cleaned}"
    )
