from __future__ import annotations

from app.utils.chat_answer_normalizer import (
    ensure_answer_question_time_context,
    ensure_answer_reference_time,
    normalize_chat_answer,
    repair_text_encoding,
)


def test_normalize_chat_answer_extracts_json_answer():
    raw = """```json
{"answer":"截至 2026-05-26 21:00 CST，主流程可用 [1]。","analysis":"证据链路已返回。","risks":["仍需扩大评测集"]}
```"""
    text = normalize_chat_answer(raw)
    assert text.startswith("截至 2026-05-26 21:00 CST")
    assert "```json" not in text
    assert '"answer"' not in text
    assert "分析依据" in text
    assert "风险与不确定性" in text


def test_repair_text_encoding_recovers_latin1_mojibake():
    broken = "æªè³ 2026-05-26ï¼ç³»ç»å¯ç¨"
    repaired = repair_text_encoding(broken)
    assert "截至 2026-05-26" in repaired
    assert "系统可用" in repaired


def test_normalize_chat_answer_repairs_json_mojibake_before_parse():
    broken = "```json\n{\"answer\":\"æªè³ 2026-05-26ï¼ç³»ç»å¯ç¨\"}\n```"
    text = normalize_chat_answer(broken)
    assert text == "截至 2026-05-26，系统可用"


def test_normalize_chat_answer_does_not_start_with_analysis_heading_when_no_answer():
    raw = """```json
{"analysis":"截至 2026-05-26 21:00 CST，主流程可用，但仍需观察长文档处理。","risks":["长文档仍需要异步队列"]}
```"""
    text = normalize_chat_answer(raw)
    assert text.startswith("截至 2026-05-26 21:00 CST")
    assert not text.startswith("**分析依据**")
    assert "风险与不确定性" in text


def test_normalize_chat_answer_prefers_nested_user_facing_summary_over_field_dump():
    raw = """```json
{"decision":{"summary":"截至 2026-07-06 10:00 CST，资料不足以支持方向性判断。","confidence":0.42},"debug":{"tool":"retrieval"}}
```"""
    text = normalize_chat_answer(raw)

    assert text.startswith("截至 2026-07-06 10:00 CST")
    assert "资料不足以支持方向性判断" in text
    assert "decision:" not in text
    assert "debug:" not in text
    assert "tool:" not in text


def test_ensure_answer_reference_time_prepends_missing_reference_time():
    text = ensure_answer_reference_time(
        "近期油价可能震荡，需要结合库存和地缘风险继续观察。",
        reference_time="2026年06月25日 19:30:00 CST",
    )

    assert text.startswith("截至 2026年06月25日 19:30:00 CST，以下分析基于当前可用资料与系统检索结果。")
    assert "近期油价可能震荡" in text


def test_ensure_answer_reference_time_does_not_duplicate_existing_reference_time():
    original = "截至 2026年06月25日 19:30:00 CST，近期油价可能震荡。"
    text = ensure_answer_reference_time(
        original,
        reference_time="2026年06月25日 19:30:00 CST",
    )

    assert text == original


def test_ensure_answer_reference_time_replaces_model_generated_leading_time():
    text = ensure_answer_reference_time(
        "截至 2026-06-25 19:30:23 CST，近期油价可能震荡。",
        reference_time="2026年06月25日 19:30:00 CST",
    )

    assert text == "截至 2026年06月25日 19:30:00 CST，近期油价可能震荡。"
    assert "19:30:23" not in text


def test_ensure_answer_question_time_context_keeps_user_as_of_date_distinct():
    text = ensure_answer_question_time_context(
        "截至 2026年07月11日 03:00:00 CST，中国区项目 Alpha 有 128 份样本 [1]。",
        question="截至 2026-06-30，中国区项目 Alpha 有多少份样本？",
        reference_time="2026年07月11日 03:00:00 CST",
    )

    assert text.startswith("截至 2026年07月11日 03:00:00 CST")
    assert "系统生成时间" in text
    assert "问题参考时间：2026-06-30" in text


def test_ensure_answer_question_time_context_does_not_duplicate_existing_date():
    original = "截至 2026年07月11日 03:00:00 CST，按 2026-06-30 的资料，样本数为 128 份 [1]。"
    text = ensure_answer_question_time_context(
        original,
        question="截至 2026-06-30，样本数是多少？",
        reference_time="2026年07月11日 03:00:00 CST",
    )

    assert text == original


def test_ensure_answer_question_time_context_ignores_questions_without_absolute_date():
    original = "截至 2026年07月11日 03:00:00 CST，近期供需仍需观察。"
    assert ensure_answer_question_time_context(
        original,
        question="近期供需如何？",
        reference_time="2026年07月11日 03:00:00 CST",
    ) == original
