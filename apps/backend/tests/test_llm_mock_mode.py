"""P2-D LLM Mock 模式单元测试

覆盖：
- OPENROUTER_MOCK=True 时跳过实际 API 调用
- 按 skill_role 返回结构化响应（chat / briefing / extraction / committee 等）
- embed() 返回确定性 mock 向量（同输入 → 同输出，已 L2 normalize）
- ping_model() 返回 success
- 默认 OPENROUTER_MOCK=False 时不影响真实路径（通过 mock client 回退验证）
- _last_usage 在 mock 模式下也正常填充（cost_scope 累计不会爆）
"""
from __future__ import annotations

import json
import math
from unittest.mock import patch

import pytest

from app.utils.llm_client import LLMClient
from app.utils.cost_scope import cost_scope


# ── OPENROUTER_MOCK=True 路径 ────────────────────────────────


def _enable_mock(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    # 测试时禁用 mock latency 避免拖慢
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)


def test_complete_with_role_mock_skip_api(monkeypatch):
    """OPENROUTER_MOCK=True 时不应调用 _get_openai_client。"""
    _enable_mock(monkeypatch)
    client = LLMClient()

    # 哪怕 OPENROUTER_API_KEY 配置了，也不会用
    with patch.object(client, "_get_openai_client") as mock_get:
        text, model = client.complete_with_role(
            skill_role="fusion",
            system_prompt="ignored",
            user_prompt="ignored",
        )
    mock_get.assert_not_called()
    assert text  # 非空
    assert model  # 非空


def test_mock_response_role_fusion_has_synthesis_narrative(monkeypatch):
    """fusion 角色（chat 路径）返回的 JSON 应含 synthesis_narrative."""
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("fusion", "sys", "user")
    parsed = json.loads(text)
    assert parsed["_mock"] is True
    assert "synthesis_narrative" in parsed
    assert isinstance(parsed["synthesis_narrative"], str)
    assert len(parsed["synthesis_narrative"]) > 5


def test_mock_response_role_extraction_has_claims(monkeypatch):
    """extraction 角色返回 ClaimExtractionSkill 期望的 {claims:[...]}."""
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("extraction", "sys", "user")
    parsed = json.loads(text)
    assert "claims" in parsed
    assert len(parsed["claims"]) >= 1
    c = parsed["claims"][0]
    for k in ("summary", "stance", "confidence", "time_horizon", "asset"):
        assert k in c, f"missing key {k}"


def test_mock_response_role_briefing_has_executive_summary(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("briefing", "sys", "user")
    parsed = json.loads(text)
    for k in ("title", "direction", "executive_summary",
              "supporting_points", "key_risks", "recommended_action"):
        assert k in parsed, f"missing key {k}"
    assert parsed["recommended_action"] in {"enter", "watch", "avoid", "hold"}


def test_mock_response_role_intent_has_mode(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("intent_recognition", "sys", "user")
    parsed = json.loads(text)
    assert parsed["mode"] in ("research", "chat", "briefing", "debate")
    assert isinstance(parsed["assets"], list)


def test_mock_response_role_speaker_has_credibility(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("speaker_profile", "sys", "user")
    parsed = json.loads(text)
    assert 0.0 <= parsed["credibility"] <= 1.0
    assert "stance_bias" in parsed


def test_mock_response_role_committee_has_position(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("committee_a", "sys", "user")
    parsed = json.loads(text)
    assert "position" in parsed
    assert isinstance(parsed["key_arguments"], list)


def test_mock_response_role_adjudication_has_verdict(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("adjudication", "sys", "user")
    parsed = json.loads(text)
    for k in ("direction", "conviction", "recommended_action", "consensus_level",
              "synthesis", "verdict", "summary", "baseline_score",
              "probability_score", "risk_score", "structure_score"):
        assert k in parsed, f"missing key {k}"


def test_mock_response_role_committee_postprocess_has_adjudication_schema(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("committee_postprocess", "sys", "user")
    parsed = json.loads(text)
    assert parsed["direction"] in {"bullish", "bearish", "neutral"}
    assert parsed["recommended_action"] in {"enter", "watch", "avoid", "hold", "scale_in", "reduce"}
    assert parsed["synthesis"]


def test_mock_response_role_query_rewrite_returns_text(monkeypatch):
    """query_rewrite 角色返回纯文本（非 JSON）— 改写后的检索查询。"""
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("query_rewrite", "sys", "user")
    # 纯文本，不能 json.loads（应该会失败）
    with pytest.raises((json.JSONDecodeError, ValueError)):
        json.loads(text)
    assert "BTC" in text or "mock" in text.lower()


def test_mock_response_unknown_role_falls_back_to_text(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    text, _ = client.complete_with_role("totally_unknown_role", "sys", "user")
    assert "Mock" in text or "mock" in text


def test_mock_records_last_usage(monkeypatch):
    """mock 模式下 _last_usage 也应填充，避免下游 cost_scope 报错。"""
    _enable_mock(monkeypatch)
    client = LLMClient()
    client.complete_with_role("fusion", "sys", "user")
    usage = client.get_last_usage()
    assert usage["mock"] is True
    assert usage["cost_usd"] == 0.0
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] > 0


def test_mock_call_reports_trace_to_cost_scope(monkeypatch):
    """mock 模式也应进入 cost_scope，保证本地演示能看到调用 trace。"""
    _enable_mock(monkeypatch)
    client = LLMClient()

    with cost_scope(tag="mock-trace") as scope:
        client.complete_with_role("fusion", "sys", "user")

    assert scope.call_count == 1
    call = scope.to_dict()["calls"][0]
    assert call["mock"] is True
    assert call["skill_role"] == "fusion"
    assert call["duration_ms"] >= 0
    assert call["prompt_hash"].startswith("sha256:")
    assert call["response_hash"].startswith("sha256:")


def test_error_classification_maps_common_provider_failures():
    client = LLMClient()

    class ApiError(Exception):
        def __init__(self, message: str, status_code: int | None = None):
            super().__init__(message)
            self.status_code = status_code

    assert client._classify_error(ApiError("unauthorized", 401)) == "auth_error"
    assert client._classify_error(ApiError("Too Many Requests", 429)) == "rate_limited"
    assert client._classify_error(ApiError("upstream timeout", 408)) == "timeout"
    assert client._classify_error(ApiError("bad gateway", 502)) == "upstream_unavailable"
    assert client._classify_error("read timeout while calling provider") == "timeout"
    assert client._classify_error("proxy connection failed") == "network_error"


def test_missing_client_trace_has_error_type(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr("app.config.settings.OPENROUTER_API_KEY", "")
    client = LLMClient()

    text, _ = client.complete_with_role("fusion", "sys", "user")
    usage = client.get_last_usage()

    assert text
    assert usage["mock"] is True
    assert usage["error"] == "openrouter_client_unavailable"
    assert usage["error_type"] == "missing_api_key"


def test_production_missing_client_does_not_return_mock(monkeypatch):
    monkeypatch.setattr("app.config.settings.APP_ENV", "production")
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr("app.config.settings.OPENROUTER_API_KEY", "")
    client = LLMClient()

    with patch.object(client, "_get_openai_client", return_value=None):
        with pytest.raises(RuntimeError, match="openrouter_client_unavailable"):
            client.complete_with_role("fusion", "sys", "user")

    usage = client.get_last_usage()
    assert usage["mock"] is False
    assert usage["error"] == "openrouter_client_unavailable"
    assert usage["error_type"] == "missing_api_key"


def test_production_all_models_failed_does_not_return_mock(monkeypatch):
    monkeypatch.setattr("app.config.settings.APP_ENV", "production")
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr("app.config.settings.OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("app.config.settings.OPENROUTER_ENABLE_MODEL_FALLBACKS", True)
    client = LLMClient()

    with patch.object(client, "_get_openai_client", return_value=object()), \
         patch.object(client, "_call_with_retry", return_value=None):
        with pytest.raises(RuntimeError, match="all_models_failed"):
            client.complete_with_role("fusion", "sys", "user")

    usage = client.get_last_usage()
    assert usage["mock"] is False
    assert usage["error"] == "all_models_failed"


def test_cost_scope_records_error_type(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr("app.config.settings.OPENROUTER_API_KEY", "")
    client = LLMClient()

    with cost_scope(tag="missing-key") as scope:
        client.complete_with_role("fusion", "sys", "user")

    call = scope.to_dict()["calls"][0]
    assert call["error_type"] == "missing_api_key"


def test_openrouter_request_uses_native_model_fallbacks_and_provider_sort(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr("app.config.settings.OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("app.config.settings.OPENROUTER_ENABLE_MODEL_FALLBACKS", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_PROVIDER_SORT", "throughput")
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "emergency/model")
    client = LLMClient()
    captured: dict = {}

    class Usage:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15

    class Message:
        content = "ok"

    class Choice:
        message = Message()

    class Response:
        model = "primary/model"
        choices = [Choice()]
        usage = Usage()

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return Response()

    class Chat:
        completions = Completions()

    class FakeClient:
        chat = Chat()

    monkeypatch.setattr(client, "_get_model_by_role", lambda role: ("primary/model", "fallback/model"))
    with patch.object(client, "_get_openai_client", return_value=FakeClient()):
        text, model = client.complete_with_role("fusion", "sys", "user")

    assert text == "ok"
    assert model == "primary/model"
    assert captured["model"] == "primary/model"
    extra_body = captured["extra_body"]
    assert extra_body["models"] == ["fallback/model", "emergency/model"]
    assert extra_body["provider"]["sort"] == "throughput"


def test_openrouter_response_actual_model_updates_usage_trace(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr("app.config.settings.OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("app.config.settings.OPENROUTER_ENABLE_MODEL_FALLBACKS", True)
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "emergency/model")
    client = LLMClient()

    class Usage:
        prompt_tokens = 20
        completion_tokens = 10
        total_tokens = 30

    class Message:
        content = "fallback answered"

    class Choice:
        message = Message()

    class Response:
        model = "fallback/model"
        choices = [Choice()]
        usage = Usage()

    class Completions:
        @staticmethod
        def create(**kwargs):
            return Response()

    class Chat:
        completions = Completions()

    class FakeClient:
        chat = Chat()

    monkeypatch.setattr(client, "_get_model_by_role", lambda role: ("primary/model", "fallback/model"))
    with patch.object(client, "_get_openai_client", return_value=FakeClient()):
        text, model = client.complete_with_role("fusion", "sys", "user")

    usage = client.get_last_usage()
    assert text == "fallback answered"
    assert model == "fallback/model"
    assert usage["model"] == "fallback/model"
    assert usage["requested_model"] == "primary/model"
    assert usage["configured_fallback_models"] == ["fallback/model", "emergency/model"]
    assert usage["fallback_chain"] == ["primary/model", "fallback/model"]


def test_openrouter_native_fallback_failure_is_not_retried_by_client(monkeypatch):
    monkeypatch.setattr("app.config.settings.APP_ENV", "production")
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr("app.config.settings.OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("app.config.settings.OPENROUTER_ENABLE_MODEL_FALLBACKS", True)
    monkeypatch.setattr("app.config.settings.REGION_FALLBACK_MODEL", "emergency/model")
    client = LLMClient()
    monkeypatch.setattr(client, "_get_model_by_role", lambda role: ("primary/model", "fallback/model"))

    with patch.object(client, "_get_openai_client", return_value=object()), \
         patch.object(client, "_call_with_retry", return_value=None) as call:
        with pytest.raises(RuntimeError, match="all_models_failed"):
            client.complete_with_role("fusion", "sys", "user")

    call.assert_called_once()
    kwargs = call.call_args.kwargs
    assert kwargs["extra_body"]["models"] == ["fallback/model", "emergency/model"]


# ── embed() mock 路径 ────────────────────────────────────────


def test_embed_mock_returns_correct_dim(monkeypatch):
    _enable_mock(monkeypatch)
    monkeypatch.setattr("app.config.settings.EMBEDDING_DIM", 2560)
    client = LLMClient()
    vecs = client.embed(["test text"])
    assert vecs is not None
    assert len(vecs) == 1
    assert len(vecs[0]) == 2560


def test_embed_mock_deterministic(monkeypatch):
    """同输入 → 同输出（便于排查向量写入问题）."""
    _enable_mock(monkeypatch)
    monkeypatch.setattr("app.config.settings.EMBEDDING_DIM", 256)
    client = LLMClient()
    v1 = client.embed(["hello world"])[0]
    v2 = client.embed(["hello world"])[0]
    assert v1 == v2


def test_embed_mock_l2_normalized(monkeypatch):
    """L2 norm 应 ≈ 1.0（cosine 距离才合理）."""
    _enable_mock(monkeypatch)
    monkeypatch.setattr("app.config.settings.EMBEDDING_DIM", 256)
    client = LLMClient()
    v = client.embed(["arbitrary text"])[0]
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6, f"L2 norm = {norm}, not normalized"


def test_embed_mock_batch(monkeypatch):
    _enable_mock(monkeypatch)
    monkeypatch.setattr("app.config.settings.EMBEDDING_DIM", 256)
    client = LLMClient()
    texts = ["t1", "t2", "t3"]
    vecs = client.embed(texts)
    assert len(vecs) == 3
    # 不同输入 → 不同向量
    assert vecs[0] != vecs[1]
    assert vecs[1] != vecs[2]


def test_embed_mock_string_input(monkeypatch):
    """str 输入应自动 wrap 成 list."""
    _enable_mock(monkeypatch)
    monkeypatch.setattr("app.config.settings.EMBEDDING_DIM", 256)
    client = LLMClient()
    vecs = client.embed("single string")
    assert vecs is not None
    assert len(vecs) == 1


# ── ping_model() mock 路径 ────────────────────────────────────


def test_ping_model_mock_success(monkeypatch):
    _enable_mock(monkeypatch)
    client = LLMClient()
    result = client.ping_model("fusion")
    assert result["success"] is True
    assert result["response"] == "OK"
    assert result["error"] is None
    assert result["latency_ms"] >= 0


# ── OPENROUTER_MOCK=False 不破坏现有路径 ──────────────────────────


def test_complete_no_mock_when_disabled(monkeypatch):
    """默认 OPENROUTER_MOCK=False 时，应该尝试调用真实 API。

    用 monkeypatch 让 _get_openai_client 返回 None（API key 缺失场景），
    验证会降级到 skill-aware mock，保证下游结构化链路仍可跑通。
    """
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    client = LLMClient()
    with patch.object(client, "_get_openai_client", return_value=None):
        text, _ = client.complete_with_role("fusion", "sys", "user")
    parsed = json.loads(text)
    assert parsed.get("mock") is True or parsed.get("_mock") is True
    assert "synthesis_narrative" in parsed


def test_embed_no_mock_when_disabled(monkeypatch):
    """OPENROUTER_MOCK=False 时 embed 走真实路径（API key 缺失则 None）."""
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    client = LLMClient()
    with patch.object(client, "_get_openai_client", return_value=None):
        result = client.embed(["test"])
    assert result is None  # 旧路径 API key 缺失返回 None
