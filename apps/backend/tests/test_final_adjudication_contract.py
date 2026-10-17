from __future__ import annotations

from app.skills.final_adjudication_skill import AdjudicationInput, FinalAdjudicationSkill


def test_final_adjudication_accepts_scale_in_and_preserves_hedging_advice():
    result = FinalAdjudicationSkill()._parse(
        """
        {
          "direction": "bullish",
          "conviction": 0.64,
          "recommended_action": "scale_in",
          "consensus_level": "moderate_consensus",
          "synthesis": "证据支持分批增加，但仍需保留风险预算。",
          "hedging_advice": "用较小仓位分批进入，并设置跌破关键支撑后的减仓规则。"
        }
        """
    )

    assert result.error is None
    assert result.recommended_action == "scale_in"
    assert result.hedging_advice == "用较小仓位分批进入，并设置跌破关键支撑后的减仓规则。"


def test_final_adjudication_accepts_reduce_action():
    result = FinalAdjudicationSkill()._parse(
        """
        {
          "direction": "bearish",
          "conviction": 0.7,
          "recommended_action": "reduce",
          "consensus_level": "weak_consensus",
          "synthesis": "风险席位和市场结构席位都提示风险暴露过高。",
          "hedging_advice": "先降低风险敞口，再等待证据重新确认。"
        }
        """
    )

    assert result.error is None
    assert result.recommended_action == "reduce"


def test_final_adjudication_unknown_action_falls_back_to_watch():
    result = FinalAdjudicationSkill()._parse(
        """
        {
          "direction": "neutral",
          "conviction": 0.5,
          "recommended_action": "all_in",
          "consensus_level": "split",
          "synthesis": "输出包含非法动作，应降级。",
          "hedging_advice": "保持观望。"
        }
        """
    )

    assert result.error is None
    assert result.recommended_action == "watch"


def test_final_adjudication_mock_mode_returns_valid_contract(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    result = FinalAdjudicationSkill().adjudicate(
        AdjudicationInput(
            debate_id="mock-debate",
            question="Research OS MVP 演示最应该保证什么？",
            asset="GENERAL",
            rounds=[
                {
                    "role": "baseline",
                    "round_number": 1,
                    "direction": "neutral",
                    "position": "需要保证资料源范围和引用透明。",
                    "key_arguments": ["限定资料源", "引用透明"],
                    "confidence": 0.6,
                }
            ],
            activated_seats=["baseline"],
        )
    )

    assert result.error is None
    assert result.synthesis
    assert result.recommended_action in {"enter", "watch", "avoid", "hold", "scale_in", "reduce"}


def test_final_adjudication_falls_back_to_round_summary_when_synthesis_missing(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr(
        "app.skills.final_adjudication_skill.llm_client.complete_with_role_str",
        lambda **_: '{"direction":"bearish","conviction":0.62,"recommended_action":"reduce"}',
    )

    result = FinalAdjudicationSkill().adjudicate(
        AdjudicationInput(
            debate_id="fallback-debate",
            question="当前系统是否适合对外演示？",
            asset="GENERAL",
            rounds=[
                {
                    "role": "baseline",
                    "round_number": 1,
                    "direction": "neutral",
                    "position": "主流程已经可用，但证据展示和异常兜底仍要完善。",
                    "key_arguments": ["主流程可用", "证据展示需要完善"],
                    "confidence": 0.58,
                },
                {
                    "role": "risk",
                    "round_number": 1,
                    "direction": "bearish",
                    "position": "如果最终裁决为空，用户会误以为系统失效。",
                    "key_arguments": ["最终裁决不能空白"],
                    "challenges": ["需要明确降级提示"],
                    "confidence": 0.7,
                },
            ],
        )
    )

    assert result.synthesis
    assert "裁决兜底" in result.synthesis
    assert "最终裁决不能空白" in result.synthesis
    assert result.recommended_action == "watch"
    assert any("兜底摘要" in warning for warning in result.evidence_quality_warnings)
