"""A2 · Workflow 级成本累计器

问题：
    LLMClient.get_last_usage() 只返回**最近一次**调用的成本。
    Briefing / Debate 等 workflow 会连续调用 3-10 次 LLM（Plan → Fusion → Briefing
    / Committee × N 席位 × M 轮），顶层只拿到最后一次的 cost_usd，严重低估。

解决方案：
    用 contextvars-based scope 包裹一段 workflow 代码，自动累计区间内的所有
    LLM 调用成本、token 数、模型列表。async 安全，支持嵌套。

用法：
    with cost_scope() as scope:
        result = briefing_workflow.run(...)
    scope.total_cost_usd  # 累计成本
    scope.total_input_tokens / total_output_tokens
    scope.calls  # 每次调用的明细

llm_client 在每次 API 调用完成后会调用 _report_to_scope(usage)，由本模块把数字
累加到当前活跃 scope（若存在）。未开启 scope 时完全无侵入。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, List, Optional


@dataclass
class CostScope:
    """一个累计区间。"""
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    calls: List[dict] = field(default_factory=list)
    # 业务标签（如 workflow="briefing", session_id="xx"），只用于日志
    tag: Optional[str] = None

    def record(self, usage: dict) -> None:
        """由 llm_client 在每次调用后调用。usage 至少含 cost_usd / input_tokens / output_tokens / model。"""
        if not isinstance(usage, dict):
            return
        cost = usage.get("cost_usd") or 0.0
        self.total_cost_usd += float(cost)
        self.total_input_tokens += int(usage.get("input_tokens") or 0)
        self.total_output_tokens += int(usage.get("output_tokens") or 0)
        call = {
            "model": usage.get("model"),
            "skill_role": usage.get("skill_role"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost_usd": cost,
        }
        for key in (
            "requested_model",
            "configured_fallback_models",
            "duration_ms",
            "prompt_hash",
            "response_hash",
            "fallback_chain",
            "response_chars",
            "mock",
            "generation_id",
            "cost_source",
            "error",
            "error_type",
            "schema_retry_count",
        ):
            if key in usage:
                call[key] = usage.get(key)
        self.calls.append(call)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def to_dict(self) -> dict:
        return {
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "call_count": self.call_count,
            "calls": self.calls,
        }


# 当前活跃 scope（支持嵌套：内层不影响外层；但内层 record 会同时冒泡到外层）
_active_scopes: ContextVar[List[CostScope]] = ContextVar("_active_scopes", default=[])


@contextmanager
def cost_scope(tag: Optional[str] = None) -> Iterator[CostScope]:
    """进入一个成本累计区间。

    支持嵌套：内层 scope 单独累计自己的数字，同时也冒泡到外层（如 workflow → skill → call）。

    用法：
        with cost_scope(tag="briefing") as scope:
            briefing_workflow.run(...)
        print(scope.total_cost_usd)
    """
    new_scope = CostScope(tag=tag)
    current = _active_scopes.get()
    token = _active_scopes.set(current + [new_scope])
    try:
        yield new_scope
    finally:
        _active_scopes.reset(token)


def _report_usage(usage: dict) -> None:
    """由 llm_client 在每次 API 调用完成后调用。冒泡到所有活跃 scope。"""
    stack = _active_scopes.get()
    if not stack:
        return
    for scope in stack:
        scope.record(usage)


__all__ = ["CostScope", "cost_scope"]
