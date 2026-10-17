from __future__ import annotations

from app.utils.structured_quality_report import build_structured_quality_report


def _claims() -> list[dict]:
    return [
        {
            "id": "claim-inventory",
            "source_ids": ["source-inventory"],
            "summary": "2026-06-30，中国区样本库存同比下降 8%。",
            "stance": "bearish",
        },
        {
            "id": "claim-orders",
            "source_ids": ["source-orders"],
            "summary": "2026-06-30，中国区样本订单同比增长 12%。",
            "stance": "bullish",
        },
    ]


def test_structured_quality_report_attributes_each_field_to_matching_claims() -> None:
    report = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=2,
        field_names=["executive_summary", "direction", "recommended_action"],
        source_ids=["source-inventory", "source-orders"],
        claim_ids=["claim-inventory", "claim-orders"],
        field_values={
            "executive_summary": "截至 2026-06-30，中国区样本库存同比下降 8%。",
            "direction": "neutral",
            "recommended_action": "watch",
        },
        claim_catalog=_claims(),
        basis_fields=["direction"],
        policy_fields=["recommended_action"],
    )

    fields = report["evidence"]["field_evidence"]
    assert report["decision_ready"] is True
    assert fields["executive_summary"]["status"] == "supported"
    assert fields["executive_summary"]["claim_ids"] == ["claim-inventory"]
    assert fields["executive_summary"]["source_ids"] == ["source-inventory"]
    assert fields["direction"]["status"] == "inference_input_basis"
    assert fields["recommended_action"]["status"] == "policy_controlled"


def test_structured_quality_report_fails_closed_on_unsupported_quantity() -> None:
    report = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=1,
        field_names=["executive_summary"],
        source_ids=["source-inventory"],
        claim_ids=["claim-inventory"],
        field_values={
            "executive_summary": "截至 2026-06-30，中国区样本库存同比下降 9%。",
        },
        claim_catalog=[_claims()[0]],
    )

    field = report["evidence"]["field_evidence"]["executive_summary"]
    assert field["status"] == "unverified"
    assert field["claim_ids"] == []
    assert report["decision_ready"] is False
    assert {item["code"] for item in report["warnings"]} == {
        "field_evidence_unverified"
    }


def test_structured_quality_report_keeps_insufficient_scope_without_claiming_support() -> None:
    report = build_structured_quality_report(
        decision_ready=False,
        quality_filters={
            "evidence_sufficiency": "insufficient",
            "insufficient_evidence_reason": "selected_sources_query_mismatch",
        },
        field_names=["executive_summary", "recommended_action"],
        source_ids=["source-selected"],
        claim_ids=[],
        field_values={"executive_summary": "资料不足。", "recommended_action": "watch"},
    )

    fields = report["evidence"]["field_evidence"]
    assert fields["executive_summary"]["status"] == "insufficient"
    assert fields["executive_summary"]["source_ids"] == ["source-selected"]
    assert fields["recommended_action"]["status"] == "insufficient"


def test_structured_quality_report_attributes_list_items_without_cross_claim_quantity_pollution() -> None:
    claims = _claims()
    claims[0]["confidence"] = 0.9
    report = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=2,
        field_names=["supporting_points"],
        source_ids=["source-inventory", "source-orders"],
        claim_ids=["claim-inventory", "claim-orders"],
        field_values={
            "supporting_points": [
                "截至 2026-06-30，中国区样本库存同比下降 8%。",
                "截至 2026-06-30，中国区样本订单同比增长 12%。",
                "库存资料的结构化置信度为 0.9。",
            ],
        },
        claim_catalog=claims,
        inference_fields=["supporting_points"],
    )

    field = report["evidence"]["field_evidence"]["supporting_points"]
    assert report["decision_ready"] is True
    assert field["status"] == "inference_with_evidence"
    assert field["claim_ids"] == ["claim-inventory", "claim-orders"]
    assert field["source_ids"] == ["source-inventory", "source-orders"]
    assert field["matched_item_count"] == 3
    assert field["unverified_item_indexes"] == []
    assert any(
        item["attribution_kind"] == "claim_metadata_basis"
        for item in field["support_checks"]
    )


def test_structured_quality_report_marks_scope_gap_analysis_without_claiming_direct_support() -> None:
    report = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=1,
        field_names=["contrary_points"],
        source_ids=["source-inventory"],
        claim_ids=["claim-inventory"],
        field_values={
            "contrary_points": [
                "当前选定范围只有一项资料，缺少外部交叉验证。",
            ],
        },
        claim_catalog=[_claims()[0]],
        scope_fields=["contrary_points"],
    )

    field = report["evidence"]["field_evidence"]["contrary_points"]
    assert report["decision_ready"] is True
    assert field["status"] == "scope_assessment"
    assert field["claim_ids"] == []
    assert field["source_ids"] == ["source-inventory"]


def test_structured_quality_report_basis_accepts_system_provenance_but_rejects_new_score() -> None:
    claim = {
        "id": "claim-alpha",
        "source_ids": ["source-alpha"],
        "summary": "2026-06-30，中国区项目 Alpha 在第二季度完成 128 份审计样本。",
        "confidence": 0.9,
        "source_trust_weight": 0.5,
    }
    supported = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=1,
        field_names=["executive_summary"],
        source_ids=["source-alpha"],
        claim_ids=["claim-alpha"],
        field_values={
            "executive_summary": (
                "依据 1 条资料（信任权重 0.5、置信度 0.9），"
                "中国区项目 Alpha 在第二季度完成 128 份审计样本。"
            ),
        },
        claim_catalog=[claim],
        basis_fields=["executive_summary"],
    )

    assert supported["decision_ready"] is True
    assert supported["evidence"]["field_evidence"]["executive_summary"]["status"] == "inference_input_basis"

    unsupported = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=1,
        field_names=["executive_summary"],
        source_ids=["source-alpha"],
        claim_ids=["claim-alpha"],
        field_values={
            "executive_summary": (
                "中国区项目 Alpha 在第二季度完成 128 份审计样本，"
                "模型确信度为 30%。"
            ),
        },
        claim_catalog=[claim],
        basis_fields=["executive_summary"],
    )

    assert unsupported["decision_ready"] is False
    assert unsupported["evidence"]["field_evidence"]["executive_summary"]["status"] == "unverified"


def test_structured_quality_report_accepts_verified_percentage_spread_only() -> None:
    claims = [
        {
            "id": "claim-beta-up",
            "source_ids": ["source-beta-up"],
            "summary": "研究员 A 预计项目 Beta 下月需求增长 10%。",
        },
        {
            "id": "claim-beta-down",
            "source_ids": ["source-beta-down"],
            "summary": "研究员 B 预计项目 Beta 下月需求下降 6%。",
        },
    ]
    supported = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=2,
        field_names=["risk_warnings"],
        source_ids=["source-beta-up", "source-beta-down"],
        claim_ids=["claim-beta-up", "claim-beta-down"],
        field_values={
            "risk_warnings": [
                "两份判断方向相反，幅度差达到 16 个百分点。",
            ],
        },
        claim_catalog=claims,
        basis_fields=["risk_warnings"],
    )

    assert supported["decision_ready"] is True
    assert supported["evidence"]["field_evidence"]["risk_warnings"]["status"] == "inference_input_basis"

    scoped = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=2,
        field_names=["contrary_points"],
        source_ids=["source-beta-up", "source-beta-down"],
        claim_ids=["claim-beta-up", "claim-beta-down"],
        field_values={
            "contrary_points": [
                "两份判断呈现 16 个百分点的显著分歧。",
            ],
        },
        claim_catalog=claims,
        scope_fields=["contrary_points"],
    )

    assert scoped["decision_ready"] is True
    assert scoped["evidence"]["field_evidence"]["contrary_points"]["status"] == "scope_assessment"

    unsupported = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=2,
        field_names=["risk_warnings"],
        source_ids=["source-beta-up", "source-beta-down"],
        claim_ids=["claim-beta-up", "claim-beta-down"],
        field_values={
            "risk_warnings": [
                "两份判断方向相反，幅度差达到 18 个百分点。",
            ],
        },
        claim_catalog=claims,
        basis_fields=["risk_warnings"],
    )

    assert unsupported["decision_ready"] is False
    assert unsupported["evidence"]["field_evidence"]["risk_warnings"]["status"] == "unverified"


def test_structured_quality_report_labels_conditional_model_thresholds_as_assumptions() -> None:
    claim = {
        "id": "claim-demand",
        "source_ids": ["source-demand"],
        "summary": "项目 Beta 当前需求判断存在分歧。",
    }
    conditional = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=1,
        field_names=["risk_warnings"],
        source_ids=["source-demand"],
        claim_ids=["claim-demand"],
        field_values={
            "risk_warnings": [
                "若后续需求偏离达到 20%，应触发重新评估。",
            ],
        },
        claim_catalog=[claim],
        model_fields=["risk_warnings"],
    )

    field = conditional["evidence"]["field_evidence"]["risk_warnings"]
    assert conditional["decision_ready"] is True
    assert field["status"] == "model_derived_assessment"
    assert field["assumption_quantities"] == [{"item_index": 0, "values": ["20%"]}]

    asserted = build_structured_quality_report(
        decision_ready=True,
        quality_filters={"evidence_sufficiency": "claim_supported"},
        claims_used=1,
        field_names=["risk_warnings"],
        source_ids=["source-demand"],
        claim_ids=["claim-demand"],
        field_values={
            "risk_warnings": [
                "后续需求已经偏离 20%。",
            ],
        },
        claim_catalog=[claim],
        model_fields=["risk_warnings"],
    )

    assert asserted["decision_ready"] is False
    assert asserted["evidence"]["field_evidence"]["risk_warnings"]["status"] == "unverified"
