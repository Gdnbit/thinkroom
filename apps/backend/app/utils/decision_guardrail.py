from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_ACTIONABLE_RE = re.compile(
    r"(买入|加仓|重仓|建仓|入场|做多|做空|卖出|减仓|清仓|止盈|止损|enter|scale[_ -]?in|reduce|short|long)",
    flags=re.IGNORECASE,
)

_ACTIONABLE_ACTIONS = {"enter", "scale_in", "reduce", "exit", "short", "long"}
_NON_ACTIONABLE_FALLBACK = "watch"

_EVIDENCE_RISK_WARNING_CODES = {
    "no_citations",
    "global_scope",
    "available_citations_not_used",
    "uncited_material_claim",
    "citation_low_text_overlap",
    "citation_direction_conflict",
    "citations_not_used",
}

_VISIBLE_CAUTION_TERMS = (
    "资料不足",
    "信息不足",
    "证据不足",
    "无引用",
    "没有引用",
    "未找到引用",
    "需要复核",
    "不能直接作为",
    "仅作为研究线索",
    "决策护栏",
)


def _warning_messages(quality_report: Mapping[str, Any]) -> list[str]:
    warnings = quality_report.get("warnings")
    if not isinstance(warnings, list):
        return []
    messages: list[str] = []
    for item in warnings:
        if isinstance(item, dict) and isinstance(item.get("message"), str):
            messages.append(item["message"])
    return messages


def _warning_codes(quality_report: Mapping[str, Any]) -> set[str]:
    warnings = quality_report.get("warnings")
    if not isinstance(warnings, list):
        return set()
    codes: set[str] = set()
    for item in warnings:
        if isinstance(item, Mapping) and isinstance(item.get("code"), str):
            codes.add(item["code"])
    return codes


def _has_visible_caution(text: str) -> bool:
    return any(term in text for term in _VISIBLE_CAUTION_TERMS)


def _normalize_warnings(warnings: Sequence[Any] | None) -> list[str]:
    if not warnings:
        return []
    messages: list[str] = []
    for item in warnings:
        if isinstance(item, str):
            messages.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("message"), str):
            messages.append(item["message"])
    return messages


def build_structured_decision_guardrail(
    *,
    mode: str,
    recommended_action: str,
    evidence_count: int,
    source_mode: str = "live",
    warnings: Sequence[Any] | None = None,
    is_mock: bool = False,
    confidence: float | None = None,
    min_confidence: float = 0.55,
) -> dict[str, Any]:
    """Guard structured decision outputs before persistence.

    This is intentionally narrow: it only changes executable recommendations
    when the decision is not ready. Analytical text remains untouched so users
    can still inspect why a result was downgraded.
    """
    action = (recommended_action or "").strip().lower()
    normalized_source_mode = (source_mode or "live").strip().lower()
    warning_messages = _normalize_warnings(warnings)
    reasons: list[str] = []

    if evidence_count <= 0:
        reasons.append("no_evidence")
    if is_mock or normalized_source_mode == "mock":
        reasons.append("mock_or_simulated_sources")
    if warning_messages:
        reasons.append("quality_warnings")
    if confidence is not None and confidence < min_confidence:
        reasons.append("low_confidence")

    actionable = action in _ACTIONABLE_ACTIONS
    decision_ready = not reasons
    effective_action = action or _NON_ACTIONABLE_FALLBACK
    status = "passed"

    if not decision_ready and actionable:
        effective_action = _NON_ACTIONABLE_FALLBACK
        status = "downgraded"
    elif not decision_ready:
        status = "warn_only"

    return {
        "mode": mode,
        "status": status,
        "original_action": action,
        "effective_action": effective_action,
        "actionable_action_detected": actionable,
        "decision_ready": decision_ready,
        "evidence_count": max(0, evidence_count),
        "source_mode": normalized_source_mode,
        "is_mock": is_mock,
        "confidence": confidence,
        "reasons": reasons,
        "warning_messages": warning_messages,
    }


def apply_chat_decision_guardrail(reply: str, quality_report: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Downgrade actionable chat answers when quality checks fail.

    The guardrail is deliberately conservative: it does not rewrite analysis, but it
    adds a clear non-actionable boundary before any answer that combines failed quality
    checks with executable trading language.
    """
    text = (reply or "").strip()
    decision_ready = quality_report.get("decision_ready") is not False
    actionable = bool(_ACTIONABLE_RE.search(text))
    warnings = _warning_messages(quality_report)
    warning_codes = _warning_codes(quality_report)
    output_integrity_failed = bool({"prompt_leakage", "language_mismatch"} & warning_codes)

    result = {
        "status": "passed",
        "actionable_language_detected": actionable,
        "decision_ready": decision_ready,
        "reason": "",
    }
    if output_integrity_failed:
        citation_count = 0
        evidence = quality_report.get("evidence")
        if isinstance(evidence, Mapping):
            try:
                citation_count = int(evidence.get("citation_count") or 0)
            except (TypeError, ValueError):
                citation_count = 0
        reason_text = "；".join(warnings[:3]) if warnings else "本次模型输出未通过输出完整性校验"
        citation_hint = " 可展开下方引用来源复核已检索证据 [1]。" if citation_count > 0 else " 请重新生成后再参考。"
        result["status"] = "blocked_output"
        result["decision_ready"] = False
        result["reason"] = "output_integrity_failed"
        return (
            "**输出质量提示**：本次模型输出疑似包含内部提示词、输出约束或非中文异常片段，"
            f"系统已阻断其作为正式回答展示。主要原因：{reason_text}。{citation_hint}"
        ), result

    if decision_ready or not actionable:
        if not decision_ready:
            result["status"] = "warn_only"
            if warning_codes & _EVIDENCE_RISK_WARNING_CODES and not _has_visible_caution(text):
                top_reasons = "；".join(warnings[:3]) if warnings else "本次回答存在证据或质量校验未通过项"
                result["reason"] = "visible_caution_added_for_failed_quality_report"
                return (
                    "**决策护栏**：本次回答存在需要复核的质量提示，"
                    "引用证据或检索范围不足，不能直接作为正式决策依据。"
                    f"主要原因：{top_reasons}。以下内容仅作为研究线索。\n\n{text}"
                ), result
            result["reason"] = "quality_report_failed_without_actionable_language"
        return text, result

    top_reasons = "；".join(warnings[:3]) if warnings else "本次回答存在证据或质量校验未通过项"
    prefix = (
        "**决策护栏**：本次回答存在需要复核的质量提示，不能直接作为买入、卖出、加仓、减仓或建仓依据。"
        f"主要原因：{top_reasons}。以下内容仅作为研究线索。\n\n"
    )
    result["status"] = "downgraded"
    result["reason"] = "actionable_language_with_failed_quality_report"
    return prefix + text, result


__all__ = ["apply_chat_decision_guardrail", "build_structured_decision_guardrail"]
