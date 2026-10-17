"""Mode Router 单元测试：覆盖所有 7 档决策路径 + 斜杠 + force + 边界"""
from __future__ import annotations

import pytest
from app.skills.intent_recognition_skill import Intent
from app.services.mode_router import (
    decide_route,
    parse_slash_command,
    ROUTE_CHAT,
    ROUTE_BRIEFING,
    ROUTE_DEBATE,
)


def _intent(**kw) -> Intent:
    """Helper: 构造 Intent 默认值"""
    defaults = dict(
        mode="chat",
        assets=[],
        themes=[],
        time_horizon="unspecified",
        needs_private_knowledge=False,
        needs_debate=False,
        confidence=0.5,
        source="rules",
    )
    defaults.update(kw)
    return Intent(**defaults)


# ══════════════════════════════════════════════════════════════════════
# 斜杠命令解析
# ══════════════════════════════════════════════════════════════════════

class TestSlashCommand:
    def test_parse_briefing(self):
        r, cleaned = parse_slash_command("/briefing 黄金怎么看")
        assert r == ROUTE_BRIEFING
        assert cleaned == "黄金怎么看"

    def test_parse_debate(self):
        r, cleaned = parse_slash_command("/debate BTC 多空观点")
        assert r == ROUTE_DEBATE
        assert cleaned == "BTC 多空观点"

    def test_parse_chat(self):
        r, cleaned = parse_slash_command("/chat 什么是资金费率")
        assert r == ROUTE_CHAT
        assert cleaned == "什么是资金费率"

    def test_aliases(self):
        assert parse_slash_command("/brief test")[0] == ROUTE_BRIEFING
        assert parse_slash_command("/research test")[0] == ROUTE_BRIEFING
        assert parse_slash_command("/committee test")[0] == ROUTE_DEBATE

    def test_no_slash_returns_none(self):
        r, _ = parse_slash_command("黄金怎么看")
        assert r is None

    def test_unknown_slash_returns_none(self):
        r, cleaned = parse_slash_command("/unknown xxx")
        assert r is None
        assert cleaned == "/unknown xxx"

    def test_leading_whitespace(self):
        r, cleaned = parse_slash_command("  /debate  test  ")
        assert r == ROUTE_DEBATE
        assert cleaned == "test"

    def test_case_insensitive(self):
        r, _ = parse_slash_command("/Briefing test")
        assert r == ROUTE_BRIEFING


# ══════════════════════════════════════════════════════════════════════
# 强制路由（UI 显式 mode 按钮）
# ══════════════════════════════════════════════════════════════════════

class TestForceRoute:
    def test_force_chat(self):
        d = decide_route("随便问问", _intent(mode="debate", needs_debate=True, confidence=0.95), force_route=ROUTE_CHAT)
        assert d.route == ROUTE_CHAT
        assert d.force_source == "api"
        assert d.confidence == 1.0

    def test_force_briefing_overrides_intent(self):
        d = decide_route("什么是 BTC", _intent(confidence=0.9), force_route=ROUTE_BRIEFING)
        assert d.route == ROUTE_BRIEFING
        assert "用户显式指定" in d.reason

    def test_invalid_force_route_ignored(self):
        d = decide_route("随便问", _intent(), force_route="invalid")
        # 回退到自动决策 → chat
        assert d.route == ROUTE_CHAT
        assert d.force_source is None


# ══════════════════════════════════════════════════════════════════════
# 辩论路由
# ══════════════════════════════════════════════════════════════════════

class TestDebateRoute:
    def test_needs_debate_high_conf(self):
        d = decide_route(
            "BTC 现在多空分歧很大，怎么看",
            _intent(needs_debate=True, confidence=0.85, assets=["BTC"]),
        )
        assert d.route == ROUTE_DEBATE

    def test_intent_mode_debate(self):
        d = decide_route(
            "分析一下多空观点",
            _intent(mode="debate", confidence=0.8),
        )
        assert d.route == ROUTE_DEBATE

    def test_needs_debate_low_conf_downgrades(self):
        """置信度低 → 不升级 debate，走 chat"""
        d = decide_route(
            "ok",
            _intent(needs_debate=True, confidence=0.4),
        )
        assert d.route == ROUTE_CHAT


# ══════════════════════════════════════════════════════════════════════
# Briefing 路由
# ══════════════════════════════════════════════════════════════════════

class TestBriefingRoute:
    def test_decision_keyword_with_asset(self):
        """决策词 + 资产 → briefing"""
        d = decide_route(
            "BTC 可以重仓吗",
            _intent(mode="asset_analysis", assets=["BTC"], confidence=0.6),
        )
        assert d.route == ROUTE_BRIEFING
        assert "重仓" in d.reason

    def test_decision_keyword_without_asset_downgrades(self):
        """决策词但没识别资产 → 不升级"""
        d = decide_route(
            "可以重仓吗",
            _intent(confidence=0.5),  # 没有 assets
        )
        assert d.route == ROUTE_CHAT

    def test_deep_keyword(self):
        """深度关键词 → briefing"""
        d = decide_route(
            "给我一份 ETH 的详细分析",
            _intent(mode="asset_analysis", assets=["ETH"], confidence=0.6),
        )
        assert d.route == ROUTE_BRIEFING
        assert "详细" in d.reason or "研报" in d.reason or "分析" in d.reason

    def test_macro_high_conf(self):
        d = decide_route(
            "美联储下次会加息还是降息",
            _intent(mode="macro", confidence=0.85),
        )
        assert d.route == ROUTE_BRIEFING

    def test_asset_analysis_high_conf(self):
        d = decide_route(
            "黄金",
            _intent(mode="asset_analysis", assets=["GOLD"], confidence=0.8),
        )
        assert d.route == ROUTE_BRIEFING

    def test_scout_high_conf(self):
        d = decide_route(
            "找找有没有新机会",
            _intent(mode="scout", confidence=0.75),
        )
        assert d.route == ROUTE_BRIEFING


# ══════════════════════════════════════════════════════════════════════
# Chat 兜底路由
# ══════════════════════════════════════════════════════════════════════

class TestChatRoute:
    def test_default_chat(self):
        d = decide_route("什么是资金费率", _intent(mode="knowledge_query", confidence=0.8))
        assert d.route == ROUTE_CHAT

    def test_low_confidence_downgrades_to_chat(self):
        d = decide_route("ok", _intent(mode="asset_analysis", confidence=0.3))
        assert d.route == ROUTE_CHAT
        assert "置信度偏低" in d.reason

    def test_knowledge_query_goes_chat(self):
        d = decide_route("什么是 RSI", _intent(mode="knowledge_query", confidence=0.9))
        assert d.route == ROUTE_CHAT


# ══════════════════════════════════════════════════════════════════════
# 决策结构完整性
# ══════════════════════════════════════════════════════════════════════

class TestDecisionPayload:
    def test_chat_can_upgrade_to_briefing_and_debate(self):
        d = decide_route("hi", _intent())
        assert d.route == ROUTE_CHAT
        assert ROUTE_BRIEFING in d.can_upgrade_to
        assert ROUTE_DEBATE in d.can_upgrade_to
        assert d.can_downgrade_to is None

    def test_briefing_can_upgrade_to_debate_downgrade_to_chat(self):
        d = decide_route(
            "BTC 详细分析",
            _intent(mode="asset_analysis", assets=["BTC"], confidence=0.85),
        )
        assert d.route == ROUTE_BRIEFING
        assert d.can_upgrade_to == [ROUTE_DEBATE]
        assert d.can_downgrade_to == ROUTE_CHAT

    def test_debate_no_upgrade_downgrade_to_briefing(self):
        d = decide_route(
            "BTC 多空辩论",
            _intent(needs_debate=True, confidence=0.9),
        )
        assert d.route == ROUTE_DEBATE
        assert d.can_upgrade_to == []
        assert d.can_downgrade_to == ROUTE_BRIEFING

    def test_cost_estimates_present(self):
        d = decide_route("hi", _intent())
        assert d.cost_estimate_usd > 0
        assert d.cost_estimate_usd < 0.01  # chat 廉价

    def test_to_payload_roundtrip(self):
        d = decide_route(
            "BTC 详细",
            _intent(mode="asset_analysis", assets=["BTC"], confidence=0.85),
        )
        payload = d.to_payload()
        assert payload["route"] == ROUTE_BRIEFING
        assert "reason" in payload
        assert "cost_estimate_usd" in payload
        assert "can_upgrade_to" in payload

    def test_slash_cleaned_message_used(self):
        d = decide_route("/briefing 黄金怎么看", _intent())
        assert d.route == ROUTE_BRIEFING
        assert d.cleaned_message == "黄金怎么看"
        assert d.force_source == "slash"


# ══════════════════════════════════════════════════════════════════════
# 优先级：斜杠 > force > intent
# ══════════════════════════════════════════════════════════════════════

class TestPriority:
    def test_force_route_trumps_slash(self):
        """force_route 由调用方显式传入，优先级最高"""
        d = decide_route(
            "/debate BTC", _intent(), force_route=ROUTE_CHAT,
        )
        assert d.route == ROUTE_CHAT
        assert d.force_source == "api"

    def test_slash_trumps_intent(self):
        d = decide_route(
            "/chat BTC 多空辩论",
            _intent(needs_debate=True, confidence=0.95),
        )
        assert d.route == ROUTE_CHAT

    def test_debate_trumps_decision_keyword(self):
        """needs_debate=True 比 决策词+资产 更优先"""
        d = decide_route(
            "BTC 到底该重仓还是清仓，多空观点",
            _intent(needs_debate=True, mode="debate", assets=["BTC"], confidence=0.9),
        )
        assert d.route == ROUTE_DEBATE
