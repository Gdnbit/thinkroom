"""
Skill 单元测试 — 不依赖数据库，纯逻辑测试
"""
import pytest
from datetime import datetime, timezone, timedelta


# ── ClaimExtraction Skill ────────────────────────────────
class TestClaimExtractionSkill:
    def test_short_text_returns_error(self):
        from app.skills.claim_extraction_skill import ClaimExtractionSkill, ClaimExtractionInput
        skill = ClaimExtractionSkill()
        result = skill.execute(ClaimExtractionInput(
            raw_source_id="test_id",
            text_content="too short",
        ))
        assert result.error is not None
        assert result.total_extracted == 0

    def test_stance_normalization(self):
        """非标准 stance 值应被规范化"""
        from app.skills.claim_extraction_skill import ExtractedClaim
        claim = ExtractedClaim(
            summary="test",
            claim_type="macro_view",
            stance="positive",  # 非标准，应转为 bullish
            time_horizon="short",
        )
        assert claim.stance == "bullish"

    def test_stance_invalid_fallback(self):
        """完全无效的 stance 应回退到 neutral"""
        from app.skills.claim_extraction_skill import ExtractedClaim
        claim = ExtractedClaim(summary="test", claim_type="macro_view",
                               stance="completely_invalid", time_horizon="short")
        assert claim.stance == "neutral"

    def test_claim_type_normalization(self):
        """非标准 claim_type 应回退到 macro_view"""
        from app.skills.claim_extraction_skill import ExtractedClaim
        claim = ExtractedClaim(summary="test", claim_type="unknown_type",
                               stance="bullish", time_horizon="short")
        assert claim.claim_type == "macro_view"

    def test_horizon_alias(self):
        """time_horizon 别名应被规范化"""
        from app.skills.claim_extraction_skill import ExtractedClaim
        claim = ExtractedClaim(summary="test", claim_type="macro_view",
                               stance="bullish", time_horizon="near-term")
        assert claim.time_horizon == "short"

    def test_confidence_clamp(self):
        """confidence 应被 clamp 到 [0, 1]"""
        from app.skills.claim_extraction_skill import ExtractedClaim
        c_high = ExtractedClaim(summary="t", claim_type="macro_view",
                                stance="bullish", time_horizon="short", confidence=1.5)
        c_low = ExtractedClaim(summary="t", claim_type="macro_view",
                               stance="bullish", time_horizon="short", confidence=-0.3)
        assert c_high.confidence == 1.0
        assert c_low.confidence == 0.0

    def test_ensure_list_from_string(self):
        """字符串应被转为列表"""
        from app.skills.claim_extraction_skill import ExtractedClaim
        claim = ExtractedClaim(summary="t", claim_type="macro_view",
                               stance="bullish", time_horizon="short",
                               asset_scope="BTC")  # 字符串而非列表
        assert claim.asset_scope == ["BTC"]

    def test_parse_invalid_json(self):
        """无效 JSON 应返回 parse error"""
        from app.skills.claim_extraction_skill import ClaimExtractionSkill
        skill = ClaimExtractionSkill()
        result = skill._parse_response("src_id", "this is not json")
        assert result.error is not None
        assert result.total_extracted == 0

    def test_parse_valid_mock_response(self):
        """有效 JSON 应正确解析"""
        import json
        from app.skills.claim_extraction_skill import ClaimExtractionSkill
        skill = ClaimExtractionSkill()
        mock_resp = json.dumps({"claims": [{
            "summary": "BTC 看多",
            "claim_type": "asset_view",
            "stance": "bullish",
            "time_horizon": "medium",
            "confidence": 0.85,
        }]})
        result = skill._parse_response("src_id", mock_resp)
        assert result.total_extracted == 1
        assert result.claims[0].stance == "bullish"
        assert result.error is None


# ── LifecycleSkill ───────────────────────────────────────
class TestLifecycleSkill:
    def _make_claim(self, **kwargs):
        """创建测试用 ClaimCard 对象"""
        from unittest.mock import MagicMock
        claim = MagicMock()
        claim.id = "test_claim_id"
        claim.status = "active"
        claim.time_horizon = kwargs.get("time_horizon", "medium")
        claim.is_contradicted = kwargs.get("is_contradicted", False)
        claim.valid_until = kwargs.get("valid_until", None)
        claim.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        return claim

    def test_active_claim_no_update(self):
        """正常活跃的 Claim 不应被标记"""
        from app.skills.lifecycle_skill import LifecycleSkill, LifecycleScanInput
        skill = LifecycleSkill()
        claim = self._make_claim(time_horizon="medium")
        result = skill.scan(LifecycleScanInput(claims=[claim]))
        assert len(result.updates) == 0
        assert result.active_count == 1

    def test_expired_valid_until(self):
        """valid_until 已过期的 Claim 应标记为 expired"""
        from app.skills.lifecycle_skill import LifecycleSkill, LifecycleScanInput
        skill = LifecycleSkill()
        past = datetime.now(timezone.utc) - timedelta(days=1)
        claim = self._make_claim(valid_until=past)
        result = skill.scan(LifecycleScanInput(claims=[claim]))
        assert len(result.updates) == 1
        assert result.updates[0].new_status == "expired"

    def test_old_immediate_claim_expires(self):
        """immediate 类型但已超过3天的 Claim 应过期"""
        from app.skills.lifecycle_skill import LifecycleSkill, LifecycleScanInput
        skill = LifecycleSkill()
        old_date = datetime.now(timezone.utc) - timedelta(days=5)
        claim = self._make_claim(time_horizon="immediate", created_at=old_date)
        result = skill.scan(LifecycleScanInput(claims=[claim]))
        assert len(result.updates) == 1
        assert result.updates[0].new_status == "expired"

    def test_contradicted_claim_invalidated(self):
        """被矛盾标记的 Claim 应标记为 invalidated"""
        from app.skills.lifecycle_skill import LifecycleSkill, LifecycleScanInput
        skill = LifecycleSkill()
        claim = self._make_claim(is_contradicted=True)
        result = skill.scan(LifecycleScanInput(claims=[claim]))
        assert len(result.updates) == 1
        assert result.updates[0].new_status == "invalidated"

    def test_non_active_claims_skipped(self):
        """非 active 状态的 Claim 不参与扫描"""
        from app.skills.lifecycle_skill import LifecycleSkill, LifecycleScanInput
        from unittest.mock import MagicMock
        skill = LifecycleSkill()
        claim = MagicMock()
        claim.status = "expired"
        result = skill.scan(LifecycleScanInput(claims=[claim]))
        assert len(result.updates) == 0


# ── StrategyDistillationSkill ────────────────────────────
class TestStrategyDistillationSkill:
    def test_short_text_returns_error(self):
        from app.skills.strategy_distillation_skill import StrategyDistillationSkill, StrategyDistillationInput
        skill = StrategyDistillationSkill()
        result = skill.execute(StrategyDistillationInput(
            raw_source_id="test_id",
            text_content="too short",
        ))
        assert result.error is not None
        assert result.total_extracted == 0

    def test_strategy_type_normalization(self):
        from app.skills.strategy_distillation_skill import DistilledStrategy
        s = DistilledStrategy(
            strategy_name="test", strategy_type="trend-following",
            summary="test", core_logic="test logic"
        )
        assert s.strategy_type == "trend_following"

    def test_invalid_strategy_type_fallback(self):
        from app.skills.strategy_distillation_skill import DistilledStrategy
        s = DistilledStrategy(
            strategy_name="test", strategy_type="unknown_type",
            summary="test", core_logic="test logic"
        )
        assert s.strategy_type == "custom"

    def test_parse_valid_response(self):
        import json
        from app.skills.strategy_distillation_skill import StrategyDistillationSkill
        skill = StrategyDistillationSkill()
        mock_resp = json.dumps({"strategies": [{
            "strategy_name": "趋势跟踪",
            "strategy_type": "trend_following",
            "summary": "跟随趋势",
            "core_logic": "价格突破时入场",
            "entry_conditions": ["突破前高"],
            "confidence": 0.8,
        }]})
        result = skill._parse_response("src_id", mock_resp)
        assert result.total_extracted == 1
        assert result.strategies[0].strategy_name == "趋势跟踪"


# ── StrategyMatchingSkill ────────────────────────────────
class TestStrategyMatchingSkill:
    def test_insufficient_signals_returns_watch(self):
        from app.skills.strategy_matching_skill import StrategyMatchingSkill, StrategyMatchingInput
        skill = StrategyMatchingSkill()
        result = skill.evaluate(StrategyMatchingInput(
            strategy_id="s1", strategy_name="test", strategy_type="trend_following",
            entry_conditions=["条件1"], asset="BTC",
        ))
        assert result.recommended_action == "watch"
        assert result.error is not None

    def test_disqualifying_flags_force_avoid(self):
        """触发否决条件时 recommended_action 必须为 avoid"""
        import json
        from app.skills.strategy_matching_skill import StrategyMatchingSkill
        skill = StrategyMatchingSkill()
        mock_resp = json.dumps({
            "direction": "long",
            "strategy_fit_score": 0.8,
            "evidence_strength_score": 0.7,
            "overall_score": 0.75,
            "matched_conditions": ["条件1"],
            "unmatched_conditions": [],
            "disqualifying_flags": ["RSI 超过 80"],  # 触发否决
            "recommended_action": "enter",  # 即使 LLM 说 enter，也应强制 avoid
            "action_notes": "test",
            "invalidation_conditions": [],
        })
        result = skill._parse_response("s1", "BTC", mock_resp)
        assert result.recommended_action == "avoid"
        assert len(result.disqualifying_flags) > 0

    def test_scores_clamped_to_unit_range(self):
        """评分应被 clamp 到 [0, 1]"""
        import json
        from app.skills.strategy_matching_skill import StrategyMatchingSkill
        skill = StrategyMatchingSkill()
        mock_resp = json.dumps({
            "direction": "neutral",
            "strategy_fit_score": 1.5,   # 超出范围
            "evidence_strength_score": -0.2,  # 超出范围
            "overall_score": 0.5,
            "matched_conditions": [],
            "unmatched_conditions": [],
            "disqualifying_flags": [],
            "recommended_action": "watch",
            "action_notes": "",
            "invalidation_conditions": [],
        })
        result = skill._parse_response("s1", "BTC", mock_resp)
        assert result.strategy_fit_score == 1.0
        assert result.evidence_strength_score == 0.0
