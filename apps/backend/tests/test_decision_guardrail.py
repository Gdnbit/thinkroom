from __future__ import annotations

from app.utils.decision_guardrail import (
    apply_chat_decision_guardrail,
    build_structured_decision_guardrail,
)


def test_guardrail_downgrades_actionable_answer_when_quality_failed():
    guarded, result = apply_chat_decision_guardrail(
        "建议直接加仓 BTC，但需要注意波动。",
        {
            "decision_ready": False,
            "warnings": [
                {"code": "no_citations", "message": "本次回答没有可交叉引用的结构化证据。"},
            ],
        },
    )

    assert result["status"] == "downgraded"
    assert guarded.startswith("**决策护栏**")
    assert "不能直接作为买入、卖出、加仓、减仓或建仓依据" in guarded


def test_guardrail_passes_ready_actionable_answer():
    guarded, result = apply_chat_decision_guardrail(
        "在证据满足时可以考虑分批加仓。",
        {"decision_ready": True, "warnings": []},
    )

    assert result["status"] == "passed"
    assert guarded == "在证据满足时可以考虑分批加仓。"


def test_guardrail_warn_only_when_failed_answer_has_no_actionable_language():
    guarded, result = apply_chat_decision_guardrail(
        "当前信息不足，只能继续观察。",
        {"decision_ready": False, "warnings": []},
    )

    assert result["status"] == "warn_only"
    assert guarded == "当前信息不足，只能继续观察。"


def test_guardrail_adds_visible_caution_for_uncited_non_actionable_answer():
    guarded, result = apply_chat_decision_guardrail(
        "截至2026年07月05日 15:39，核心交易撮合与清算逻辑运行稳定，前端延迟约200ms。",
        {
            "decision_ready": False,
            "warnings": [
                {"code": "no_citations", "message": "本次回答没有可交叉引用的结构化证据。"},
                {"code": "global_scope", "message": "本次检索范围为全库，未限定到当前上传资料或指定来源。"},
            ],
        },
    )

    assert result["status"] == "warn_only"
    assert result["reason"] == "visible_caution_added_for_failed_quality_report"
    assert guarded.startswith("**决策护栏**")
    assert "需要复核" in guarded
    assert "仅作为研究线索" in guarded


def test_guardrail_blocks_output_integrity_failure_without_showing_bad_text():
    guarded, result = apply_chat_decision_guardrail(
        "截至 2026年06月22日 03:48:00 CST，This means the [输出约束] was actually leaked [1]。",
        {
            "decision_ready": False,
            "evidence": {"citation_count": 1},
            "warnings": [
                {"code": "prompt_leakage", "message": "回答疑似包含内部提示词或输出约束残片。"},
                {"code": "language_mismatch", "message": "中文问题下出现大段英文异常片段。"},
            ],
        },
    )

    assert result["status"] == "blocked_output"
    assert result["reason"] == "output_integrity_failed"
    assert guarded.startswith("**输出质量提示**")
    assert "疑似包含内部提示词" in guarded
    assert "[1]" in guarded
    assert "This means" not in guarded


def test_structured_guardrail_downgrades_actionable_recommendation_without_evidence():
    result = build_structured_decision_guardrail(
        mode="briefing",
        recommended_action="enter",
        evidence_count=0,
        source_mode="live",
        warnings=[],
        is_mock=False,
    )

    assert result["status"] == "downgraded"
    assert result["effective_action"] == "watch"
    assert result["decision_ready"] is False
    assert "no_evidence" in result["reasons"]


def test_structured_guardrail_downgrades_actionable_recommendation_on_mock_sources():
    result = build_structured_decision_guardrail(
        mode="committee",
        recommended_action="scale_in",
        evidence_count=3,
        source_mode="mock",
        warnings=[],
        is_mock=True,
    )

    assert result["status"] == "downgraded"
    assert result["effective_action"] == "watch"
    assert result["decision_ready"] is False
    assert "mock_or_simulated_sources" in result["reasons"]


def test_structured_guardrail_passes_non_actionable_watch_without_evidence():
    result = build_structured_decision_guardrail(
        mode="briefing",
        recommended_action="watch",
        evidence_count=0,
        source_mode="live",
        warnings=[],
        is_mock=False,
    )

    assert result["status"] == "warn_only"
    assert result["effective_action"] == "watch"
    assert result["decision_ready"] is False
