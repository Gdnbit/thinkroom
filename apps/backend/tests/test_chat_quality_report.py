from __future__ import annotations

from app.api.v1.chat import (
    _apply_selected_source_prompt_constraints,
    _build_grounded_integrity_fallback,
    _build_integrity_citations_payload,
    _should_use_grounded_prompt,
)
from app.utils.chat_quality_report import (
    build_chat_quality_report,
    find_direction_conflicting_citations,
    find_low_overlap_citations,
    find_missing_citation_quantitative_details,
    find_uncited_material_claims,
    remove_unsupported_self_certification_sentences,
    prune_low_overlap_citations,
)


def test_grounded_prompt_is_used_for_source_only_context_without_claim_citations():
    assert _should_use_grounded_prompt(
        context_block="## 当前限定资料范围\n- 用户上传资料片段",
        citation_count=0,
    ) is True


def test_grounded_prompt_is_not_used_for_empty_context():
    assert _should_use_grounded_prompt(context_block="", citation_count=0) is False


def test_low_overlap_citation_guardrail_flags_obvious_misattribution():
    low = find_low_overlap_citations(
        "截至 2026年06月19日，BTC 技术面明显走强并突破阻力 [1]。",
        [{
            "index": 1,
            "summary": "原油库存上升压制油价",
            "short_quote": "EIA 原油库存连续上升，油价承压下行。",
        }],
    )

    assert low == [1]


def test_low_overlap_citation_guardrail_allows_related_paraphrase():
    low = find_low_overlap_citations(
        "截至 2026年06月19日，原油库存继续增加，因此油价仍然承压 [1]。",
        [{
            "index": 1,
            "summary": "原油库存上升压制油价",
            "short_quote": "EIA 原油库存连续上升，油价承压下行。",
        }],
    )

    assert low == []


def test_prune_low_overlap_citations_removes_only_extra_sentence_citations():
    reply = (
        "截至 2026年07月05日，本次验收标记为 RO_E2E_123 [1][2]。\n"
        "系统上传后需要展示五个阶段：接收、解析、切片、向量化、入库 [1][2]。"
    )
    citations = [
        {
            "index": 1,
            "summary": "回答必须原样写出验收标记",
            "short_quote": "Research OS E2E 验收标记:RO_E2E_123",
        },
        {
            "index": 2,
            "summary": "系统上传后展示五个处理阶段",
            "short_quote": "系统应该展示五个可见阶段:接收、解析、切片、向量化、入库。",
        },
    ]

    cleaned = prune_low_overlap_citations(reply, citations)

    assert "RO_E2E_123 [1]" in cleaned
    assert "入库 [2]" in cleaned
    assert "[1][2]" not in cleaned
    assert find_low_overlap_citations(cleaned, citations) == []


def test_prune_low_overlap_citations_keeps_single_low_overlap_citation_for_warning():
    reply = "截至 2026年07月05日，BTC 技术面走强 [1]。"
    citations = [{
        "index": 1,
        "summary": "原油库存上升压制油价",
        "short_quote": "EIA 原油库存连续上升，油价承压下行。",
    }]

    assert prune_low_overlap_citations(reply, citations) == reply
    assert find_low_overlap_citations(reply, citations) == [1]


def test_quality_report_treats_source_scope_reference_as_grounding():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年07月06日 00:16 CST，根据刚上传的 E2E 测试文件：\n"
            "本次验收标记为：RO_E2E_123 [S1]\n"
            "系统上传后需要展示的五个阶段依次为：接收、解析、切片、向量化、入库 [2]。\n"
            "上述回答严格限定于本次上传的测试资料源，不涉及任何投资建议 [S1]。"
        ),
        citation_count=2,
        citations_payload=[
            {
                "index": 1,
                "claim_id": "claim-1",
                "summary": "回答必须原样写出验收标记",
                "short_quote": "Research OS E2E 验收标记:RO_E2E_123",
                "evidence_span_id": "span-1",
            },
            {
                "index": 2,
                "claim_id": "claim-2",
                "summary": "系统上传后展示五个处理阶段",
                "short_quote": "系统应该展示五个可见阶段:接收、解析、切片、向量化、入库。",
                "evidence_span_id": "span-2",
            },
        ],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1", "title": "fixture.txt"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=100,
        run_id="run-1",
        generated_at="2026-07-06T00:16:00+08:00",
        reference_time="2026年07月06日 00:16 CST",
    )

    assert report["evidence"]["uncited_material_claim_count"] == 0
    assert report["evidence"]["used_source_scope_references"] == [1]


def test_quality_report_allows_e2e_receipt_answer_without_false_quality_blockers():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年07月06日 01:01 CST，基于您刚上传的 E2E 测试文件，回答如下：\n"
            "本次验收标记：RO_E2E_20260705170103_CCJ47S [1]\n"
            "系统上传后需展示的五个阶段：接收、解析、切片、向量化、入库 [2]。\n"
            "以上内容严格限定在本次上传的资料源范围内，验收标记已按要求原样写出 [1]。"
            "该文件明确说明“只用于系统验收，不应被视为任何投资建议”。"
        ),
        citation_count=2,
        citations_payload=[
            {
                "index": 1,
                "claim_id": "claim-1",
                "summary": "预期回答部分明确要求原样写出验收标记并限定证据来源",
                "short_quote": "预期回答:当用户询问本文件时,回答必须原样写出验收标记,并列出五个阶段,证据必须限定在本次上传的资料源。",
                "evidence_span_id": "span-1",
            },
            {
                "index": 2,
                "claim_id": "claim-2",
                "summary": "决策事实部分列出五个可见阶段",
                "short_quote": "决策事实:用户上传资料后,系统应该展示五个可见阶段:接收、解析、切片、向量化、入库。",
                "evidence_span_id": "span-2",
            },
        ],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1", "title": "research-os-e2e.txt"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=100,
        run_id="run-e2e-receipt-answer",
        generated_at="2026-07-05T17:01:51+00:00",
        reference_time="2026年07月06日 01:01 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert "citation_low_text_overlap" not in warning_codes
    assert "uncited_material_claim" not in warning_codes
    assert report["decision_ready"] is True


def test_self_certification_cleanup_removes_uncited_environment_validity_tail():
    reply = (
        "截至 2026年07月06日，根据刚上传的资料，批量验收标记为 RO_BATCH_E2E_123 [1]。\n"
        "这三份文件共同验证多文件上传、同时排队、受控 dispatcher 和引用证据 [1]。"
        "当前测试环境未触发失效条件（测试环境重置或验收标记更新），上述判断有效。"
    )

    cleaned = remove_unsupported_self_certification_sentences(reply)

    assert "当前测试环境未触发失效条件" not in cleaned
    assert "上述判断有效" not in cleaned
    assert "RO_BATCH_E2E_123 [1]" in cleaned

    report = build_chat_quality_report(
        reply=cleaned,
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "claim-1",
            "summary": "批量验收文件说明了验收标记和批量链路验证内容",
            "short_quote": "批量验收标记:RO_BATCH_E2E_123。用于验证多文件上传、同时排队、受控 dispatcher 和引用证据。",
            "evidence_span_id": "span-1",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1", "title": "batch.txt"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=100,
        run_id="run-self-cert-cleanup",
        generated_at="2026-07-06T05:50:00+00:00",
        reference_time="2026年07月06日 13:50 CST",
    )

    assert report["evidence"]["uncited_material_claim_count"] == 0
    assert report["decision_ready"] is True


def test_self_certification_cleanup_keeps_cited_environment_status():
    reply = "当前测试环境未触发失效条件，上述判断有效 [1]。"

    assert remove_unsupported_self_certification_sentences(reply) == reply


def test_quality_report_marks_evidence_supported_answer_as_usable():
    report = build_chat_quality_report(
        reply="截至 2026年06月19日 12:00:00 CST，结论需要保守看待 [1]。",
        citation_count=1,
        citations_payload=[{"evidence_span_id": "span-1"}],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1234,
        run_id="run-1",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    assert report["decision_ready"] is True
    assert report["severity"] == "ok"
    assert report["run_id"] == "run-1"
    assert report["evidence"]["citation_count"] == 1
    assert report["evidence"]["evidence_span_count"] == 1
    assert report["evidence"]["low_overlap_citation_count"] == 0
    assert report["warnings"] == []


def test_quality_report_requires_review_for_low_overlap_citation():
    report = build_chat_quality_report(
        reply="截至 2026年06月19日 12:00:00 CST，BTC 技术面明显走强并突破阻力 [1]。",
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "claim-1",
            "summary": "原油库存上升压制油价",
            "short_quote": "EIA 原油库存连续上升，油价承压下行。",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1234,
        run_id="run-low-overlap",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    assert report["decision_ready"] is False
    assert report["evidence"]["low_overlap_citations"] == [1]
    assert {item["code"] for item in report["warnings"]} == {"citation_low_text_overlap"}


def test_quality_report_blocks_direction_conflicting_citation():
    report = build_chat_quality_report(
        reply="截至 2026年06月19日 12:00:00 CST，原油库存上升，但油价将走强上涨 [1]。",
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "claim-1",
            "summary": "原油库存上升，油价承压下跌",
            "short_quote": "EIA 原油库存连续上升，油价承压下行。",
            "evidence_span_id": "span-1",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1234,
        run_id="run-direction-conflict",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    assert report["decision_ready"] is False
    assert report["evidence"]["direction_conflict_citations"] == [1]
    assert "citation_direction_conflict" in {item["code"] for item in report["warnings"]}


def test_quality_report_requires_review_when_available_citations_are_not_used():
    report = build_chat_quality_report(
        reply="截至 2026年06月19日 12:00:00 CST，结论需要保守看待，但回答没有显式标注引用来源。",
        citation_count=2,
        citations_payload=[
            {"index": 1, "evidence_span_id": "span-1", "summary": "证据一"},
            {"index": 2, "evidence_span_id": "span-2", "summary": "证据二"},
        ],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1234,
        run_id="run-unused-citations",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    assert report["decision_ready"] is False
    assert report["evidence"]["citation_count"] == 2
    assert report["evidence"]["used_citation_count"] == 0
    assert report["evidence"]["unused_citation_count"] == 2
    assert {item["code"] for item in report["warnings"]} == {"available_citations_not_used"}


def test_quality_report_counts_source_fallback_marker_as_used_citation():
    report = build_chat_quality_report(
        reply="截至 2026年07月05日 17:41:55 CST，本次验收标记为 RO_E2E_TEST [S1]。",
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "source:src-1",
            "source_id": "src-1",
            "summary": "资料源：research-os-e2e.txt",
            "short_quote": "本次验收标记为 RO_E2E_TEST。",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="google/gemini-3.1-pro-preview",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1234,
        run_id="run-source-fallback-used",
        generated_at="2026-07-05T09:41:55+00:00",
        reference_time="2026年07月05日 17:41:55 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert report["evidence"]["used_citations"] == [1]
    assert report["evidence"]["used_source_fallback_citations"] == [1]
    assert "available_citations_not_used" not in warning_codes
    assert "source_fallback_only" in warning_codes


def test_quality_report_blocks_uncited_material_claims_even_when_some_citations_are_used():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年06月19日 12:00:00 CST，原油库存上升会压制油价 [1]。"
            "因此下周可以加仓原油多单。"
        ),
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "evidence_span_id": "span-1",
            "summary": "原油库存上升压制油价",
            "short_quote": "EIA 原油库存连续上升，油价承压下行。",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1234,
        run_id="run-uncited-material-claim",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    assert report["decision_ready"] is False
    assert report["evidence"]["uncited_material_claim_count"] == 1
    assert "下周可以加仓原油多单" in report["evidence"]["uncited_material_claims"][0]
    assert "uncited_material_claim" in {item["code"] for item in report["warnings"]}


def test_quality_report_blocks_prompt_leakage_and_language_mismatch():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年06月22日 03:48:00 CST，This means the [输出约束] was actually "
            "part of the Assistant's previous message, not a normal user-facing answer [1]。"
        ),
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "summary": "SCOPE_ACCEPTANCE_ALPHA 检索验证状态为中性",
            "short_quote": "该证据用于 scoped retrieval acceptance。",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=26000,
        run_id="run-prompt-leak",
        generated_at="2026-06-21T19:48:00+00:00",
        reference_time="2026年06月22日 03:48:00 CST",
    )

    assert report["decision_ready"] is False
    warning_codes = {item["code"] for item in report["warnings"]}
    assert "prompt_leakage" in warning_codes
    assert "language_mismatch" in warning_codes


def test_quality_report_warns_on_weak_or_unverifiable_answer():
    report = build_chat_quality_report(
        reply="近期油价可能震荡。",
        citation_count=0,
        citations_payload=[],
        invalid_citations=[3],
        stance_mismatches=[{"index": 1}],
        model_used="mock",
        scope_payload={"mode": "global", "source_ids": []},
        source_scope={},
        quality_filters={"exclude_expired": False, "exclude_contradicted": False},
        duration_ms=0,
        run_id="run-2",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    assert report["decision_ready"] is False
    assert report["severity"] == "warning"
    warning_codes = {item["code"] for item in report["warnings"]}
    assert "no_citations" in warning_codes
    assert "invalid_citations_removed" in warning_codes
    assert "citation_stance_mismatch" in warning_codes
    assert "mock_or_error_model" in warning_codes
    assert "missing_reference_time_in_answer" in warning_codes
    assert "global_scope" in warning_codes
    assert "quality_filter_disabled" in warning_codes


def test_quality_report_requires_review_when_global_answer_has_no_citations():
    report = build_chat_quality_report(
        reply="截至 2026年06月19日 12:00:00 CST，当前信息不足，建议先等待更多证据再判断。",
        citation_count=0,
        citations_payload=[],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "global", "source_ids": []},
        source_scope={},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-global-no-citations",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert "no_citations" in warning_codes
    assert report["decision_ready"] is False


def test_quality_report_requires_review_for_source_fallback_only_citation():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年06月19日 12:00:00 CST，资料显示铜库存紧张，"
            "所以下周铜价判断需要保守看多 [1]。"
        ),
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "source:src-1",
            "source_id": "src-1",
            "summary": "资料源：copper next-week directional note",
            "short_quote": "铜库存紧张，下周铜价判断需要保守看多。",
            "evidence_span_id": None,
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-source-fallback-only",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert "source_fallback_only" in warning_codes
    assert report["evidence"]["source_fallback_count"] == 1
    assert report["evidence"]["evidence_span_count"] == 0
    assert report["decision_ready"] is False


def test_quality_report_requires_review_when_answer_uses_source_fallback_citation():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年06月19日 12:00:00 CST，结构化观点显示铜库存偏紧 [1]。"
            "同时，原文补充说明下周仍需保守看多 [2]。"
        ),
        citation_count=2,
        citations_payload=[
            {
                "index": 1,
                "claim_id": "claim-1",
                "source_id": "src-1",
                "summary": "铜库存偏紧",
                "short_quote": "铜库存偏紧。",
                "evidence_span_id": "span-1",
            },
            {
                "index": 2,
                "claim_id": "source:src-1",
                "source_id": "src-1",
                "summary": "资料源：copper next-week directional note",
                "short_quote": "原文补充说明下周仍需保守看多。",
                "evidence_span_id": None,
            },
        ],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-mixed-source-fallback-used",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert "source_fallback_used" in warning_codes
    assert report["evidence"]["used_source_fallback_count"] == 1
    assert report["decision_ready"] is False


def test_quality_report_allows_unused_source_fallback_when_structured_citation_is_used():
    report = build_chat_quality_report(
        reply="截至 2026年06月19日 12:00:00 CST，结构化观点显示铜库存偏紧 [1]。",
        citation_count=2,
        citations_payload=[
            {
                "index": 1,
                "claim_id": "claim-1",
                "source_id": "src-1",
                "summary": "铜库存偏紧",
                "short_quote": "铜库存偏紧。",
                "evidence_span_id": "span-1",
            },
            {
                "index": 2,
                "claim_id": "source:src-1",
                "source_id": "src-1",
                "summary": "资料源：copper next-week directional note",
                "short_quote": "原文补充说明下周仍需保守看多。",
                "evidence_span_id": None,
            },
        ],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="anthropic/claude-opus-4.6",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-1"]},
        source_scope={"sources": [{"source_id": "src-1"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-unused-source-fallback",
        generated_at="2026-06-19T04:00:00+00:00",
        reference_time="2026年06月19日 12:00:00 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert "source_fallback_used" not in warning_codes
    assert report["evidence"]["used_source_fallback_count"] == 0
    assert report["decision_ready"] is True


def test_quality_report_marks_explicit_evidence_abstention_not_decision_ready():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年07月11日 02:40:00 CST，当前资料没有项目 Zeta 的预算数据，"
            "因此无法判断预算是否超支 [1]。"
        ),
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "claim-zeta",
            "source_id": "src-zeta",
            "summary": "资料不包含项目 Zeta 预算数据",
            "short_quote": "该资料不包含项目 Zeta 或预算信息。",
            "evidence_span_id": "span-zeta",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-zeta"]},
        source_scope={"sources": [{"source_id": "src-zeta"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-explicit-abstention",
        generated_at="2026-07-10T18:40:00+00:00",
        reference_time="2026年07月11日 02:40:00 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert report["decision_ready"] is False
    assert report["evidence_sufficiency"] == "insufficient"
    assert report["insufficient_evidence_reason"] == "answer_declared_insufficient_evidence"
    assert "answer_declared_insufficient_evidence" in warning_codes


def test_quality_report_does_not_treat_late_conflict_caveat_as_whole_answer_abstention():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年07月11日，两份资料对项目 Beta 的判断明显不一致 [1][2]。"
            "研究员 A 预计增长 10% [1]，研究员 B 预计下降 6% [2]。"
            "两份资料未给出底层数据，当前无法判断哪一方更可能成立。"
        ),
        citation_count=2,
        citations_payload=[
            {"index": 1, "claim_id": "claim-a", "source_id": "src-a", "summary": "增长 10%", "short_quote": "增长 10%"},
            {"index": 2, "claim_id": "claim-b", "source_id": "src-b", "summary": "下降 6%", "short_quote": "下降 6%"},
        ],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-a", "src-b"]},
        source_scope={"sources": [{"source_id": "src-a"}, {"source_id": "src-b"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-conflict-caveat",
        generated_at="2026-07-10T18:40:00+00:00",
        reference_time="2026年07月11日 02:40:00 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert report["evidence_sufficiency"] != "insufficient"
    assert report["insufficient_evidence_reason"] is None
    assert "answer_declared_insufficient_evidence" not in warning_codes


def test_quality_report_does_not_treat_conditional_evidence_policy_as_current_abstention():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年07月11日 18:46:07 CST，根据当前选定资料，系统主流程要求严格仅使用用户"
            "明确选择的信息源，并在关键判断后标注引用 [1]。该资料强调：在证据不足以支撑结论时，"
            "系统必须明确说明知识边界，不得凭空推断 [1]。下一步应持续核查输出与选定资料的一致性 [1]。"
        ),
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "claim-policy",
            "source_id": "src-policy",
            "summary": "系统必须仅使用选定信息源；当证据不足以支撑结论时，应明确说明知识边界，不得凭空推断。",
            "short_quote": "严格限定选定资料，并在证据不足时明确说明边界。",
            "evidence_span_id": "span-policy",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-policy"]},
        source_scope={"sources": [{"source_id": "src-policy"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-conditional-policy",
        generated_at="2026-07-11T10:46:07+00:00",
        reference_time="2026年07月11日 18:46:07 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert report["evidence_sufficiency"] != "insufficient"
    assert report["insufficient_evidence_reason"] is None
    assert "answer_declared_insufficient_evidence" not in warning_codes


def test_source_scope_marker_cannot_ground_an_unrelated_material_recommendation():
    report = build_chat_quality_report(
        reply=(
            "截至 2026年07月11日 19:30:00 CST，资料确认库存记录已完成更新 [1]。"
            "因此建议立即重仓买入并扩大风险敞口 [S1]。"
        ),
        citation_count=1,
        citations_payload=[{
            "index": 1,
            "claim_id": "claim-inventory",
            "source_id": "src-inventory",
            "summary": "库存记录已完成更新",
            "short_quote": "库存记录更新完毕。",
            "evidence_span_id": "span-inventory",
        }],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-inventory"]},
        source_scope={"sources": [{"source_id": "src-inventory"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=1200,
        run_id="run-source-scope-bypass",
        generated_at="2026-07-11T11:30:00+00:00",
        reference_time="2026年07月11日 19:30:00 CST",
    )

    warning_codes = {item["code"] for item in report["warnings"]}
    assert report["decision_ready"] is False
    assert report["evidence"]["uncited_material_claim_count"] == 1
    assert "uncited_material_claim" in warning_codes


def test_direction_checker_ignores_english_substrings_temporal_long_and_negation():
    assert find_direction_conflicting_citations(
        "截至 2026年07月11日，供应紧缺可能推动价格上涨 [1]。",
        [{"index": 1, "summary": "Supply shortage supports higher prices."}],
    ) == []
    assert find_direction_conflicting_citations(
        "截至 2026年07月11日，价格可能下降 [1]。",
        [{"index": 1, "summary": "Long-term contract duration is twelve months."}],
    ) == []
    assert find_direction_conflicting_citations(
        "截至 2026年07月11日，风险并不支持看空结论 [1]。",
        [{"index": 1, "summary": "The outlook is not bearish."}],
    ) == []


def test_quantitative_citation_details_cannot_be_replaced_by_generic_conflict_summary():
    citations = [
        {"index": 1, "summary": "研究员 A 预计项目 Beta 下月需求增长 10%。"},
        {"index": 2, "summary": "研究员 B 预计项目 Beta 下月需求下降 6%。"},
    ]

    assert find_missing_citation_quantitative_details(
        "两份研究报告对项目 Beta 的短期需求判断存在显著分歧 [1][2]。",
        citations,
    ) == [1, 2]
    assert find_missing_citation_quantitative_details(
        "研究员 A 预计增长 10% [1]，研究员 B 预计下降 6% [2]。",
        citations,
    ) == []


def test_quality_report_blocks_used_citation_when_quantitative_fact_is_omitted():
    report = build_chat_quality_report(
        reply="截至 2026年07月11日，两份研究报告对项目 Beta 的判断存在显著分歧 [1][2]。",
        citation_count=2,
        citations_payload=[
            {"index": 1, "claim_id": "claim-a", "summary": "研究员 A 预计需求增长 10%。"},
            {"index": 2, "claim_id": "claim-b", "summary": "研究员 B 预计需求下降 6%。"},
        ],
        invalid_citations=[],
        stance_mismatches=[],
        model_used="z-ai/glm-5",
        scope_payload={"mode": "selected_sources", "source_ids": ["src-a", "src-b"]},
        source_scope={"sources": [{"source_id": "src-a"}, {"source_id": "src-b"}]},
        quality_filters={"exclude_expired": True, "exclude_contradicted": True},
        duration_ms=100,
        run_id="run-quantitative-detail",
        generated_at="2026-07-11T12:00:00+00:00",
        reference_time="2026年07月11日 20:00:00 CST",
    )

    assert report["decision_ready"] is False
    assert report["evidence"]["quantitative_detail_missing_citations"] == [1, 2]
    assert "citation_quantitative_detail_missing" in {item["code"] for item in report["warnings"]}


def test_selected_source_prompt_blocks_unsupported_inference():
    prompt = _apply_selected_source_prompt_constraints("base prompt", ["src-1"])

    assert "选定资料严格边界" in prompt
    assert "不得从库存、订单" in prompt
    assert "价格方向、因果机制、市场前景或操作建议" in prompt
    assert "不自行选择赢家" in prompt
    assert _apply_selected_source_prompt_constraints("base prompt", []) == "base prompt"


def test_grounded_fallback_explicitly_labels_opposite_evidence_as_inconsistent():
    citations = [
        {"index": 1, "stance": "bullish", "summary": "研究员 A 预计需求增长 10%"},
        {"index": 2, "stance": "bearish", "summary": "研究员 B 预计需求下降 6%"},
    ]
    answer = _build_grounded_integrity_fallback(
        citations,
        reference_time="2026年07月11日 03:00:00 CST",
        question="两份资料对项目 Beta 的需求判断是否一致？",
    )

    assert "不一致" in answer
    assert "[1][2]" in answer
    assert "增长 10% [1]" in answer
    assert "下降 6% [2]" in answer
    assert find_low_overlap_citations(answer, citations) == []


def test_uncited_quantitative_summary_is_material_even_when_detail_is_later_cited():
    reply = (
        "中国区样本库存同比下降 8%，样本订单同比增长 12%。"
        "库存同比下降 8% [1]，订单同比增长 12% [2]。"
    )

    claims = find_uncited_material_claims(reply, citation_count=2)

    assert len(claims) == 1
    assert "下降 8%" in claims[0]
    assert "增长 12%" in claims[0]


def test_integrity_citation_payload_preserves_stance_for_conflict_fallback():
    citation = type(
        "CitationFixture",
        (),
        {
            "index": 1,
            "claim_id": "claim-1",
            "source_id": "source-1",
            "stance": "bearish",
            "summary": "需求下降 6%",
            "short_quote": "预计需求下降 6%",
            "evidence_span_id": "span-1",
        },
    )()

    payload = _build_integrity_citations_payload([citation])

    assert payload == [{
        "index": 1,
        "claim_id": "claim-1",
        "source_id": "source-1",
        "stance": "bearish",
        "summary": "需求下降 6%",
        "short_quote": "预计需求下降 6%",
        "evidence_span_id": "span-1",
    }]


def test_conflict_synthesis_allows_joint_opposite_direction_citations():
    reply = "截至 2026年07月11日，两份资料对项目 Beta 的判断明显不一致 [1][2]。"
    citations = [
        {"index": 1, "summary": "项目 Beta 下月需求增长 10%", "short_quote": "需求增长 10%。"},
        {"index": 2, "summary": "项目 Beta 下月需求下降 6%", "short_quote": "需求下降 6%。"},
    ]

    assert find_low_overlap_citations(reply, citations) == []


def test_process_recommendation_is_not_an_uncited_material_decision_claim():
    claims = find_uncited_material_claims(
        "建议进一步核实两位研究员采用的数据和假设前提。",
        citation_count=2,
    )

    assert claims == []
