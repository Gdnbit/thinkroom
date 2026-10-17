"""G14 单元测试 · query_rewrite_skill

覆盖：
- should_rewrite 规则层（短/长/指代/空）
- rewrite() 触发与不触发的两条路径
- LLM 失败 fallback
- LLM 返回带前缀/引号 时的清洗
- 长改写 截断
- 配置开关 ENABLE_QUERY_REWRITE
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.skills.query_rewrite_skill import (
    QueryRewriteSkill,
    RewriteResult,
    query_rewrite_skill,
)


# ── should_rewrite ──────────────────────────────────────────


def test_should_rewrite_empty_returns_false():
    s = QueryRewriteSkill()
    ok, _ = s.should_rewrite("", history=[])
    assert ok is False
    ok, _ = s.should_rewrite("   ", history=[])
    assert ok is False


def test_should_rewrite_short_query_triggers():
    s = QueryRewriteSkill()
    # 默认阈值 12 字符
    for q in ["BTC 怎么看", "ETH？", "什么情况", "多空"]:
        ok, reason = s.should_rewrite(q, history=[])
        assert ok is True, f"{q!r} should trigger"
        assert "short" in reason.lower()


def test_should_rewrite_long_query_skips():
    s = QueryRewriteSkill()
    long_q = "BTC 在最近一周的价格走势如何？技术指标和市场情绪如何"
    ok, reason = s.should_rewrite(long_q, history=[])
    assert ok is False
    assert "long enough" in reason


def test_should_rewrite_referential_with_history_triggers():
    s = QueryRewriteSkill()
    # 长问题但含指代代词 + 历史不空 → 触发
    history = [
        {"role": "user", "content": "BTC 怎么看"},
        {"role": "assistant", "content": "上涨趋势..."},
    ]
    for q in ["那它呢？", "刚才说的会持续多久", "上面提到的逻辑还成立吗"]:
        ok, reason = s.should_rewrite(q, history=history)
        assert ok is True, f"{q!r} should trigger"
        assert "referential" in reason


def test_should_rewrite_referential_without_history_no_trigger():
    s = QueryRewriteSkill()
    # 含指代但无历史 — LLM 也无法解析，跳过
    q = "上面提到的逻辑还成立吗这是一个比较长的问题"
    ok, _ = s.should_rewrite(q, history=[])
    assert ok is False  # 长度够 + 无历史 = 跳过


def test_should_rewrite_disabled_via_settings(monkeypatch):
    s = QueryRewriteSkill()
    monkeypatch.setattr("app.config.settings.ENABLE_QUERY_REWRITE", False)
    ok, reason = s.should_rewrite("BTC?", history=[])
    assert ok is False
    assert "disabled" in reason


# ── rewrite() ──────────────────────────────────────────────


def test_rewrite_skip_returns_original():
    s = QueryRewriteSkill()
    long_q = "BTC 在最近一周的价格走势如何？技术指标和市场情绪如何"
    r = s.rewrite(long_q, history=[])
    assert r.triggered is False
    assert r.source == "rule_skip"
    assert r.rewritten == long_q.strip()
    assert r.original == long_q.strip()


def test_rewrite_short_query_calls_llm():
    s = QueryRewriteSkill()
    fake_response = "BTC 当前价格走势 技术指标 市场情绪 综合分析"
    with patch("app.utils.llm_client.llm_client") as mock_client:
        mock_client.complete_with_role_str.return_value = fake_response
        mock_client.get_last_usage.return_value = {
            "model": "rewrite-model",
            "duration_ms": 240,
            "cost_usd": 0.001,
        }
        r = s.rewrite("BTC 怎么看", history=[])

    assert r.triggered is True
    assert r.source == "llm"
    assert r.rewritten == fake_response
    assert r.original == "BTC 怎么看"
    assert r.usage == {
        "model": "rewrite-model",
        "duration_ms": 240,
        "cost_usd": 0.001,
    }
    mock_client.complete_with_role_str.assert_called_once()


def test_rewrite_strips_quotes_and_prefixes():
    s = QueryRewriteSkill()
    samples = [
        ('"BTC 走势 综合分析"', "BTC 走势 综合分析"),
        ('改写：BTC 当前走势', "BTC 当前走势"),
        ('查询：ETH 技术指标', "ETH 技术指标"),
        ('rewrite: BTC bullish', "BTC bullish"),
        ("`BTC 趋势`", "BTC 趋势"),
    ]
    for raw, expected in samples:
        with patch("app.utils.llm_client.llm_client") as mock_client:
            mock_client.complete_with_role_str.return_value = raw
            r = s.rewrite("BTC?", history=[])
        assert r.rewritten == expected, f"{raw!r} → {r.rewritten!r}, expected {expected!r}"


def test_rewrite_truncates_overlong():
    s = QueryRewriteSkill()
    very_long = "X" * 500
    with patch("app.utils.llm_client.llm_client") as mock_client:
        mock_client.complete_with_role_str.return_value = very_long
        r = s.rewrite("BTC?", history=[], max_chars=200)
    assert len(r.rewritten) == 200


def test_rewrite_empty_response_falls_back():
    s = QueryRewriteSkill()
    with patch("app.utils.llm_client.llm_client") as mock_client:
        mock_client.complete_with_role_str.return_value = "   "
        r = s.rewrite("BTC?", history=[])
    assert r.triggered is True
    assert r.source == "llm_fallback"
    assert r.rewritten == "BTC?"


def test_rewrite_llm_exception_falls_back():
    s = QueryRewriteSkill()
    with patch("app.utils.llm_client.llm_client") as mock_client:
        mock_client.complete_with_role_str.side_effect = RuntimeError("boom")
        r = s.rewrite("BTC?", history=[])
    assert r.triggered is True
    assert r.source == "llm_fallback"
    assert r.rewritten == "BTC?"
    assert "llm error" in r.reason


def test_rewrite_uses_recent_history_only(monkeypatch):
    """history 截取最近 N 轮，避免 prompt 失控."""
    s = QueryRewriteSkill()
    monkeypatch.setattr("app.config.settings.QUERY_REWRITE_HISTORY_TURNS", 2)
    history = [
        {"role": "user", "content": f"q{i}"} for i in range(20)
    ]
    captured_user_prompt = {}

    def _fake_complete(**kwargs):
        captured_user_prompt["text"] = kwargs.get("user_prompt", "")
        return "rewritten"

    with patch("app.utils.llm_client.llm_client") as mock_client:
        mock_client.complete_with_role_str.side_effect = _fake_complete
        s.rewrite("它呢", history=history)

    # 应只包含最近 4 条（2 轮 × 2 messages）
    user_prompt = captured_user_prompt.get("text", "")
    # 旧的 q0 / q5 不应出现
    assert "q0" not in user_prompt
    assert "q5" not in user_prompt
    # 最新的 q19 应该出现
    assert "q19" in user_prompt


# ── 全局 singleton ────────────────────────────────────────


def test_global_singleton_exists():
    assert isinstance(query_rewrite_skill, QueryRewriteSkill)
