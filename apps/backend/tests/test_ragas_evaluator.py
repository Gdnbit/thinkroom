"""G16 RAGAS 评估器单元测试

覆盖：
- 工具函数：_strip_json / _safe_json / _cosine / _avg_precision_at_k
- 各指标 mock 模式（OPENROUTER_MOCK=1）：返回有效结构
- 各指标 LLM 失败：返回 None + errors
- evaluate_all 一次性跑通三项
- /api/v1/evaluate 端点 200 + schema
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.services.ragas_evaluator import (
    RAGASResult,
    _avg_precision_at_k,
    _cosine,
    _safe_json,
    _strip_json,
    evaluate_all,
    evaluate_answer_relevance,
    evaluate_context_precision,
    evaluate_faithfulness,
)


# ── 1. 工具函数 ───────────────────────────────────────────────────


def test_strip_json_handles_code_fence():
    assert _strip_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_json('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_json('{"a": 1}') == '{"a": 1}'
    assert _strip_json('  {"a": 1}  ') == '{"a": 1}'


def test_safe_json_invalid_returns_none():
    assert _safe_json("not json") is None
    assert _safe_json('{"valid": true}') == {"valid": True}


def test_cosine_basic():
    assert _cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine([1, 0], [-1, 0]) == pytest.approx(0.0)  # clamp 到 [0,1]
    assert _cosine([], [1]) == 0.0


def test_avg_precision_at_k_all_relevant():
    """[T,T,T] → AP = (1/1 + 2/2 + 3/3) / 3 = 1.0."""
    assert _avg_precision_at_k([True, True, True]) == pytest.approx(1.0)


def test_avg_precision_at_k_none_relevant():
    assert _avg_precision_at_k([False, False, False]) == 0.0


def test_avg_precision_at_k_mixed():
    """[T, F, T] → AP = (1/1 + 0 + 2/3) / 2 = 0.833..."""
    assert _avg_precision_at_k([True, False, True]) == pytest.approx(5 / 6)


def test_avg_precision_at_k_empty():
    assert _avg_precision_at_k([]) == 0.0


# ── 2. Mock 模式（OPENROUTER_MOCK=1）── 三个指标返回有效结果 ─────


def test_evaluate_faithfulness_mock_returns_score(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    score, n_claims, n_supp, errs = evaluate_faithfulness(
        "What is BTC?",
        "BTC is digital gold and a store of value.",
        ["BTC was created in 2009 as a decentralized currency.",
         "Many investors view it as digital gold."],
    )
    # mock 返回 supported=[True, True] → 1.0
    assert score == pytest.approx(1.0)
    assert n_claims == 2
    assert n_supp == 2
    assert errs == []


def test_evaluate_relevance_mock_returns_score(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    score, errs = evaluate_answer_relevance(
        "What is BTC?",
        "BTC is digital gold and a store of value.",
    )
    # mock embedding 是确定性的；不同文本 cosine 在 [0, 1)
    assert score is not None
    assert 0.0 <= score <= 1.0


def test_evaluate_precision_mock_returns_score(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    score, n_rel, n_total, errs = evaluate_context_precision(
        "What is BTC?",
        ["BTC is decentralized.", "Cats are mammals.", "Energy markets vary."],
    )
    # mock 返回 verdicts=[T, T, F] → AP = (1/1 + 2/2) / 2 = 1.0
    assert score == pytest.approx(1.0)
    assert n_rel == 2
    assert n_total == 3


def test_evaluate_all_mock_returns_full_result(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    result = evaluate_all(
        "What is BTC?",
        "BTC is digital gold and a store of value.",
        ["BTC was created in 2009.", "Many call it digital gold."],
    )
    assert isinstance(result, RAGASResult)
    assert result.is_mock is True
    assert result.faithfulness is not None
    assert result.answer_relevance is not None
    assert result.context_precision is not None
    assert result.latency_ms >= 0
    assert result.judge_model  # 应有值


# ── 3. LLM 失败路径 → None + errors ──────────────────────────────


def test_evaluate_faithfulness_llm_exception(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)

    def _raise(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.utils.llm_client.llm_client.complete_with_role", _raise)
    score, n_claims, n_supp, errs = evaluate_faithfulness(
        "Q", "A", ["ctx"]
    )
    assert score is None
    assert n_claims == 0
    assert any("LLM call failed" in e for e in errs)


def test_evaluate_relevance_invalid_json(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr(
        "app.utils.llm_client.llm_client.complete_with_role",
        lambda *a, **kw: ("not json output", "deepseek"),
    )
    score, errs = evaluate_answer_relevance("Q", "A")
    assert score is None
    assert any("invalid JSON" in e for e in errs)


def test_evaluate_precision_invalid_verdicts_type(monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr(
        "app.utils.llm_client.llm_client.complete_with_role",
        lambda *a, **kw: (json.dumps({"verdicts": "not a list"}), "deepseek"),
    )
    score, n_rel, n_total, errs = evaluate_context_precision("Q", ["c1", "c2"])
    assert score is None
    assert n_total == 0
    assert any("verdicts not a list" in e for e in errs)


# ── 4. 边界 ───────────────────────────────────────────────────────


def test_evaluate_faithfulness_empty_inputs():
    score, n_claims, n_supp, errs = evaluate_faithfulness("Q", "", [])
    assert score is None
    assert "empty" in errs[0]


def test_evaluate_precision_empty_contexts():
    score, n_rel, n_total, errs = evaluate_context_precision("Q", [])
    assert score is None
    assert "empty" in errs[0]


def test_evaluate_faithfulness_zero_claims_extracted(monkeypatch):
    """LLM 返回空 claims 列表 → score=None."""
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", False)
    monkeypatch.setattr(
        "app.utils.llm_client.llm_client.complete_with_role",
        lambda *a, **kw: (json.dumps({"claims": [], "supported": []}), "deepseek"),
    )
    score, n_claims, n_supp, errs = evaluate_faithfulness("Q", "A", ["ctx"])
    assert score is None
    assert n_claims == 0


# ── 5. /api/v1/evaluate 端点 ──────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """构造 FastAPI test client（绕过 API key 中间件）."""
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.APP_API_KEY", "")  # 关闭认证
    from app.main import app
    return TestClient(app)


def test_evaluate_endpoint_returns_full_schema(client):
    resp = client.post(
        "/api/v1/evaluate",
        json={
            "question": "What is BTC?",
            "answer": "BTC is digital gold.",
            "contexts": ["BTC was created in 2009.", "It's a cryptocurrency."],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "faithfulness" in data
    assert "answer_relevance" in data
    assert "context_precision" in data
    assert "judge_model" in data
    assert "latency_ms" in data
    assert "is_mock" in data
    assert data["is_mock"] is True
    assert data["faithfulness"] is not None


def test_evaluate_endpoint_validates_input(client):
    """空 contexts → 422."""
    resp = client.post(
        "/api/v1/evaluate",
        json={
            "question": "Q",
            "answer": "A",
            "contexts": [],
        },
    )
    assert resp.status_code == 422


def test_evaluate_endpoint_too_long_input(client):
    """超长 contexts → 422."""
    resp = client.post(
        "/api/v1/evaluate",
        json={
            "question": "Q",
            "answer": "A",
            "contexts": ["c"] * 100,  # 超 max_length=50
        },
    )
    assert resp.status_code == 422
