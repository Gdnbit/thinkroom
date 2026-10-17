from __future__ import annotations

import json

from app.skills.committee_debate_skill import CommitteeDebateSkill, DebateRoundInput
from app.skills.final_adjudication_skill import AdjudicationInput, FinalAdjudicationSkill


def test_committee_seat_uses_configured_generation_parameters(monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.COMMITTEE_MODEL_C_TEMPERATURE", 0.07)
    monkeypatch.setattr("app.config.settings.COMMITTEE_MODEL_C_MAX_TOKENS", 777)
    captured: dict = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "position": "风险仍需复核",
                "key_arguments": ["证据一"],
                "challenges": [],
                "concessions": [],
                "confidence": 0.6,
                "direction": "neutral",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.skills.committee_debate_skill.llm_client.complete_with_role_str", fake_complete)

    result = CommitteeDebateSkill().debate_round(
        DebateRoundInput(
            debate_id="debate-1",
            round_number=1,
            role="risk",
            question="风险是什么？",
            asset="GENERAL",
        )
    )

    assert result.error is None
    assert captured["skill_role"] == "committee_c"
    assert captured["temperature"] == 0.07
    assert captured["max_tokens"] == 777


def test_committee_seat_passes_explicit_model_override(monkeypatch) -> None:
    captured: dict = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "position": "基准判断",
                "key_arguments": [],
                "challenges": [],
                "concessions": [],
                "confidence": 0.5,
                "direction": "neutral",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.skills.committee_debate_skill.llm_client.complete_with_role_str", fake_complete)

    CommitteeDebateSkill().debate_round(
        DebateRoundInput(
            debate_id="debate-override",
            round_number=1,
            role="baseline",
            question="基准判断是什么？",
            asset="GENERAL",
            model_override="custom/provider-model",
        )
    )

    assert captured["model"] == "custom/provider-model"


def test_adjudication_uses_configured_generation_parameters(monkeypatch) -> None:
    monkeypatch.setattr("app.config.settings.COMMITTEE_POSTPROCESS_TEMPERATURE", 0.08)
    monkeypatch.setattr("app.config.settings.COMMITTEE_POSTPROCESS_MAX_TOKENS", 1666)
    captured: dict = {}

    def fake_complete(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "direction": "neutral",
                "conviction": 0.5,
                "recommended_action": "watch",
                "consensus_level": "weak_consensus",
                "synthesis": "证据仍有限，维持观察并等待进一步确认。",
                "baseline_score": 0.5,
                "probability_score": 0.5,
                "risk_score": 0.5,
                "structure_score": 0.5,
                "key_agreements": [],
                "key_disagreements": [],
                "structure_warnings": [],
                "risk_warnings": [],
                "evidence_quality_warnings": [],
                "invalidation_conditions": [],
                "hedging_advice": "",
                "action_economics": {},
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.skills.final_adjudication_skill.llm_client.complete_with_role_str", fake_complete)

    result = FinalAdjudicationSkill().adjudicate(
        AdjudicationInput(
            debate_id="debate-2",
            question="最终结论是什么？",
            asset="GENERAL",
            rounds=[],
        )
    )

    assert result.error is None
    assert captured["skill_role"] == "committee_postprocess"
    assert captured["temperature"] == 0.08
    assert captured["max_tokens"] == 1666
