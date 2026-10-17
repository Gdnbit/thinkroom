from __future__ import annotations

import json

from app.skills.briefing_skill import BriefingInput, BriefingSkill
from app.skills.fusion_skill import FusionOutput


def _fusion(asset: str) -> FusionOutput:
    return FusionOutput(
        asset=asset,
        direction="neutral",
        conviction=0.35,
        setup_quality="low",
        key_supporting_factors=["选定资料给出一项可核验证据"],
        key_risk_factors=["资料覆盖有限"],
        synthesis_narrative="当前只能形成范围受限的研究判断。",
        recommended_action="watch",
        time_horizon="short",
    )


def _valid_briefing_json() -> str:
    return json.dumps(
        {
            "title": "资料范围说明",
            "topic": "证据边界",
            "executive_summary": "当前资料只支持范围受限的研究判断。",
            "supporting_points": ["存在一项可核验证据（私域观点）"],
            "contrary_points": ["缺少外部交叉验证（证据边界）"],
            "key_risks": ["资料覆盖有限（证据边界）"],
            "strategy_fit_summary": "前提 -> 证据 -> 判断 -> 风险 -> 待验证条件",
            "recommended_action": "watch",
            "invalidation_conditions": ["补充相反证据后重新评估（证据边界）"],
        },
        ensure_ascii=False,
    )


def test_general_evidence_briefing_uses_compact_profile_and_runtime_settings(monkeypatch):
    captured: dict = {}

    def fake_complete_with_role(**kwargs):
        captured.update(kwargs)
        return _valid_briefing_json(), "test/general-model"

    monkeypatch.setattr("app.skills.briefing_skill.llm_client.complete_with_role", fake_complete_with_role)
    monkeypatch.setattr("app.config.settings.BRIEFING_GENERAL_MAX_TOKENS", 777)
    monkeypatch.setattr("app.config.settings.BRIEFING_TEMPERATURE", 0.12)

    result = BriefingSkill().execute(
        BriefingInput(
            question="只根据选定资料说明系统证据边界",
            asset="GENERAL",
            fusion_result=_fusion("GENERAL"),
        )
    )

    assert result.model_used == "test/general-model"
    assert captured["max_tokens"] == 777
    assert captured["temperature"] == 0.12
    assert "证据逻辑链" in captured["system_prompt"]
    assert "不得把融合层确信度" in captured["system_prompt"]
    assert "情景排列组合推演" not in captured["system_prompt"]


def test_financial_briefing_keeps_full_scenario_profile(monkeypatch):
    captured: dict = {}

    def fake_complete_with_role(**kwargs):
        captured.update(kwargs)
        return _valid_briefing_json(), "test/financial-model"

    monkeypatch.setattr("app.skills.briefing_skill.llm_client.complete_with_role", fake_complete_with_role)
    monkeypatch.setattr("app.config.settings.BRIEFING_MAX_TOKENS", 2222)
    monkeypatch.setattr("app.config.settings.BRIEFING_TEMPERATURE", 0.18)

    BriefingSkill().execute(
        BriefingInput(
            question="分析 BTC 的交易风险",
            asset="BTC",
            fusion_result=_fusion("BTC"),
            market_state={"price": 100_000},
        )
    )

    assert captured["max_tokens"] == 2222
    assert captured["temperature"] == 0.18
    assert "情景排列组合推演" in captured["system_prompt"]
