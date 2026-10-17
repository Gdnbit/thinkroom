"""B2: LLM 调用 + JSON 解析 + Schema 校验的一次性重试封装

问题：即使带 JSON schema + robust_json_parse，仍有 2-5% 概率 LLM
产出无法装配到 Pydantic 模型的内容（缺字段、类型错、嵌套错）。
本模块提供"失败即重试 1 次 + 附加纠错指令"的通用包装。

预期把 2-5% 失败率降到 <0.1%（两次独立 LLM 调用都失败的概率）。

用法示例::

    result = call_with_schema_retry(
        llm_call=lambda sys, usr: llm_client.complete_with_role_str(
            skill_role="briefing",
            system_prompt=sys,
            user_prompt=usr,
            response_format=BRIEFING_SCHEMA,
        ),
        system_prompt=BRIEFING_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        parser=lambda text: robust_json_parse(text, context="BriefingSkill"),
        validator=lambda d: BriefingOutput(**d),
        context="BriefingSkill",
    )
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional, TypeVar

from loguru import logger

T = TypeVar("T")


class RetryableParseError(Exception):
    """所有重试均失败时抛出。调用方应 fallback 到降级对象或返回错误。"""


# 可被"失败重试"捕获的异常集合
_RETRYABLE_EXCEPTIONS: tuple = (
    json.JSONDecodeError,
    ValueError,
    KeyError,
    TypeError,
)

# Pydantic ValidationError 动态导入（避免硬依赖）
try:
    from pydantic import ValidationError as _PydanticValidationError  # type: ignore
    _RETRYABLE_EXCEPTIONS = _RETRYABLE_EXCEPTIONS + (_PydanticValidationError,)
except ImportError:  # pragma: no cover
    pass


def call_with_schema_retry(
    *,
    llm_call: Callable[[str, str], str],
    system_prompt: str,
    user_prompt: str,
    parser: Callable[[str], Any],
    validator: Callable[[Any], T],
    context: str = "",
    max_retries: int = 1,
) -> T:
    """调用 LLM，解析 + 校验输出；失败时自动重试并附加纠错指令。

    Args:
        llm_call: (system, user) -> text 的闭包，内部自行处理模型选择/fallback
        system_prompt: system 部分（重试时不变）
        user_prompt: 初始 user prompt（重试时会追加错误反馈）
        parser: text -> dict/list，可抛异常
        validator: parsed -> T，可抛异常（通常是 Pydantic 校验）
        context: 日志标签，如 "BriefingSkill"
        max_retries: 额外重试次数（默认 1 → 最多调用 2 次）

    Returns:
        validator 返回的对象

    Raises:
        RetryableParseError: 所有尝试均失败
    """
    current_user = user_prompt
    last_error: Optional[BaseException] = None

    for attempt in range(max_retries + 1):
        try:
            raw = llm_call(system_prompt, current_user)
            data = parser(raw)
            return validator(data)
        except _RETRYABLE_EXCEPTIONS as e:
            last_error = e
            err_snippet = str(e)[:300].replace("\n", " ")
            if attempt < max_retries:
                logger.warning(
                    f"[{context or 'llm_retry'}] attempt {attempt+1}/{max_retries+1} "
                    f"failed: {type(e).__name__}: {err_snippet}; retrying with stricter prompt"
                )
                current_user = _augment_prompt_with_error(user_prompt, err_snippet)
                continue
            logger.error(
                f"[{context or 'llm_retry'}] all {max_retries+1} attempts failed; "
                f"last error: {type(e).__name__}: {err_snippet}"
            )
            break

    raise RetryableParseError(
        f"[{context or 'llm_retry'}] all {max_retries+1} attempts failed; last error: {last_error}"
    ) from last_error


def _augment_prompt_with_error(original_prompt: str, err_snippet: str) -> str:
    """在原 prompt 末尾追加错误反馈和更严格的指令。"""
    return (
        original_prompt
        + "\n\n"
        + "## 上次输出解析失败 / Previous attempt failed\n"
        + f"错误：{err_snippet}\n\n"
        + "## 严格要求（Strict requirements）\n"
        + "- 只返回合法 JSON，不要任何前后缀文字\n"
        + "- 不要使用 ```markdown``` 代码块围栏\n"
        + "- 所有必填字段都必须给出非空、类型正确的值\n"
        + "- 数组字段用 `[]`，对象字段用 `{}`，绝不能是字符串里的 JSON\n"
    )


__all__ = [
    "call_with_schema_retry",
    "RetryableParseError",
]
