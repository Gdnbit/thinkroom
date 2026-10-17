from __future__ import annotations


def test_rule_only_intent_never_calls_llm(monkeypatch):
    from app.skills.intent_recognition_skill import IntentRecognitionSkill

    skill = IntentRecognitionSkill()

    def unexpected_llm(_question: str):
        raise AssertionError("rule-only intent recognition must not call an LLM")

    monkeypatch.setattr(skill, "_llm_layer", unexpected_llm)

    result = skill.recognize("ambiguous opaque request", rule_only=True)

    assert result.source == "rules"
    assert result.mode == "chat"
