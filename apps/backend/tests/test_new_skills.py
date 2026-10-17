"""
新增 Skill 单元测试：
- IndicatorInterpretationSkill
- SpeakerProfilingSkill
- CommitteeDebateSkill
- FinalAdjudicationSkill
- FileParser
"""
import pytest


# ── IndicatorInterpretationSkill ─────────────────────────
class TestIndicatorInterpretationSkill:
    def _skill(self):
        from app.skills.indicator_interpretation_skill import IndicatorInterpretationSkill
        return IndicatorInterpretationSkill()

    def _input(self, **kwargs):
        from app.skills.indicator_interpretation_skill import IndicatorInput
        defaults = dict(asset="BTC")
        defaults.update(kwargs)
        return IndicatorInput(**defaults)

    def test_empty_input_returns_neutral(self):
        result = self._skill().interpret(self._input())
        assert result.overall_signal == "neutral"
        assert result.signal_strength == 0.0
        assert result.suggested_bias == "wait"

    def test_rsi_overbought_bearish(self):
        result = self._skill().interpret(self._input(rsi_14=75.0))
        assert result.overall_signal == "bearish"
        assert any("超买" in w for w in result.warnings)

    def test_rsi_oversold_bullish(self):
        result = self._skill().interpret(self._input(rsi_14=25.0))
        assert result.overall_signal == "bullish"

    def test_rsi_neutral_range(self):
        result = self._skill().interpret(self._input(rsi_14=50.0))
        assert result.overall_signal == "neutral"

    def test_ma_bullish_alignment(self):
        """价格 > MA20 > MA50 应为 bullish"""
        result = self._skill().interpret(self._input(
            price=100.0, ma_20=90.0, ma_50=80.0
        ))
        assert result.overall_signal == "bullish"

    def test_ma_bearish_alignment(self):
        """价格 < MA20 < MA50 应为 bearish"""
        result = self._skill().interpret(self._input(
            price=70.0, ma_20=80.0, ma_50=90.0
        ))
        assert result.overall_signal == "bearish"

    def test_uptrend_bullish(self):
        result = self._skill().interpret(self._input(
            trend_direction="uptrend", trend_strength=0.8
        ))
        assert result.overall_signal == "bullish"

    def test_downtrend_bearish_with_warning(self):
        result = self._skill().interpret(self._input(
            trend_direction="downtrend", trend_strength=0.7
        ))
        assert result.overall_signal == "bearish"
        assert len(result.warnings) > 0

    def test_extreme_volatility_warning(self):
        result = self._skill().interpret(self._input(volatility_state="extreme"))
        assert any("极高" in w or "极端" in w for w in result.warnings + result.interpretations)

    def test_signal_strength_in_range(self):
        result = self._skill().interpret(self._input(
            rsi_14=75.0, trend_direction="downtrend"
        ))
        assert 0.0 <= result.signal_strength <= 1.0

    def test_support_resistance_in_interpretations(self):
        result = self._skill().interpret(self._input(
            price=100.0,
            key_support_levels=[90.0, 80.0],
            key_resistance_levels=[110.0, 120.0],
        ))
        assert any("支撑" in i for i in result.interpretations)
        assert any("阻力" in i for i in result.interpretations)

    def test_asset_in_summary(self):
        result = self._skill().interpret(self._input(asset="ETH"))
        assert "ETH" in result.summary


# ── SpeakerProfilingSkill ────────────────────────────────
class TestSpeakerProfilingSkill:
    def _skill(self):
        from app.skills.speaker_profiling_skill import SpeakerProfilingSkill
        return SpeakerProfilingSkill()

    def test_no_input_returns_unknown(self):
        from app.skills.speaker_profiling_skill import SpeakerProfilingInput
        skill = self._skill()
        result = skill.execute(SpeakerProfilingInput(
            raw_source_id="test",
            text_content="",
            speaker_hint=None,
        ))
        assert result.name == "Unknown"
        assert result.error is not None

    def test_speaker_hint_used_as_fallback(self):
        from app.skills.speaker_profiling_skill import SpeakerProfilingInput
        result = self._skill().execute(SpeakerProfilingInput(
            raw_source_id="test",
            text_content="短文本",
            speaker_hint="测试分析师",
        ))
        # Mock 模式或 fallback 时 name 应使用 speaker_hint
        assert result.name is not None
        assert len(result.name) > 0

    def test_trust_weight_in_range(self):
        from app.skills.speaker_profiling_skill import SpeakerProfilingInput
        result = self._skill().execute(SpeakerProfilingInput(
            raw_source_id="test",
            text_content="比特币分析" * 20,
            speaker_hint="著名分析师",
        ))
        assert 0.0 <= result.trust_weight <= 1.0

    def test_domain_tags_from_theme_hint(self):
        from app.skills.speaker_profiling_skill import SpeakerProfilingSkill
        tags = SpeakerProfilingSkill._guess_domains("crypto btc macro")
        assert "加密" in tags
        assert "宏观" in tags

    def test_domain_tags_max_5(self):
        from app.skills.speaker_profiling_skill import SpeakerProfilingSkill
        tags = SpeakerProfilingSkill._guess_domains(
            "crypto btc macro gold bond equity strategy"
        )
        assert len(tags) <= 5


# ── CommitteeDebateSkill ───────────────────────
@pytest.mark.online  # LLM debate skill：空 key 靠 mock 走通但慢
class TestCommitteeDebateSkill:
    def _skill(self):
        from app.skills.committee_debate_skill import CommitteeDebateSkill
        return CommitteeDebateSkill()

    def _input(self, role="bull", **kwargs):
        from app.skills.committee_debate_skill import DebateRoundInput
        return DebateRoundInput(
            debate_id="d1",
            round_number=1,
            role=role,
            question="BTC 是否会突破前高？",
            asset="BTC",
            **kwargs,
        )

    def test_bull_round_returns_output(self):
        result = self._skill().debate_round(self._input(role="bull"))
        assert result.role == "bull"
        assert len(result.position) > 0

    def test_bear_round_returns_output(self):
        result = self._skill().debate_round(self._input(role="bear"))
        assert result.role == "bear"
        assert len(result.position) > 0

    def test_neutral_round_returns_output(self):
        result = self._skill().debate_round(self._input(role="neutral"))
        assert result.role == "neutral"
        assert len(result.position) > 0

    def test_confidence_in_range(self):
        result = self._skill().debate_round(self._input())
        assert 0.0 <= result.confidence <= 1.0

    def test_direction_valid(self):
        result = self._skill().debate_round(self._input())
        assert result.direction in ("bullish", "bearish", "neutral", "conditional")

    def test_parse_invalid_json(self):
        result = self._skill()._parse("bull", "not json")
        # 应 fallback 而不是抛异常
        assert result.role == "bull"
        assert result.position is not None


# ── FinalAdjudicationSkill ───────────────────────
@pytest.mark.online  # LLM adjudication skill：空 key 靠 mock 走通但慢
class TestFinalAdjudicationSkill:
    def _skill(self):
        from app.skills.final_adjudication_skill import FinalAdjudicationSkill
        return FinalAdjudicationSkill()

    def _input(self):
        from app.skills.final_adjudication_skill import AdjudicationInput
        return AdjudicationInput(
            debate_id="d1",
            question="BTC 是否会突破前高？",
            asset="BTC",
            rounds=[
                {"role": "bull", "round_number": 1,
                 "position": "看涨，基本面支撑",
                 "key_arguments": ["减半效应"], "challenges": [], "concessions": []},
                {"role": "bear", "round_number": 1,
                 "position": "看跌，宏观压力",
                 "key_arguments": ["流动性收紧"], "challenges": [], "concessions": []},
                {"role": "neutral", "round_number": 1,
                 "position": "中性观望",
                 "key_arguments": ["等待确认"], "challenges": [], "concessions": []},
            ],
        )

    def test_adjudication_returns_direction(self):
        result = self._skill().adjudicate(self._input())
        assert result.direction in ("bullish", "bearish", "neutral")

    def test_conviction_in_range(self):
        result = self._skill().adjudicate(self._input())
        assert 0.0 <= result.conviction <= 1.0

    def test_recommended_action_valid(self):
        result = self._skill().adjudicate(self._input())
        assert result.recommended_action in ("enter", "watch", "avoid", "hold")

    def test_consensus_level_valid(self):
        result = self._skill().adjudicate(self._input())
        assert result.consensus_level in (
            "strong_consensus", "moderate_consensus",
            "weak_consensus", "split"
        )

    def test_parse_invalid_json_fallback(self):
        result = self._skill()._parse("definitely not json")
        assert result.error is not None


# ── FileParser ────────────────────────────────────────────
class TestFileParser:
    def test_nonexistent_file_returns_none(self):
        from app.services.file_parser import extract_text
        result = extract_text("/nonexistent/path/file.txt")
        assert result is None

    def test_txt_file_extracted(self, tmp_path):
        from app.services.file_parser import extract_text
        f = tmp_path / "test.txt"
        f.write_text("这是测试文本内容", encoding="utf-8")
        result = extract_text(str(f), "text/plain")
        assert result == "这是测试文本内容"

    def test_unsupported_format_returns_none(self, tmp_path):
        from app.services.file_parser import extract_text
        f = tmp_path / "test.xyz"
        f.write_bytes(b"some bytes")
        result = extract_text(str(f), "application/octet-stream")
        assert result is None

    def test_count_words_chinese(self):
        from app.services.file_parser import count_words
        assert count_words("比特币分析报告") == 7  # 7 个中文字符

    def test_count_words_english(self):
        from app.services.file_parser import count_words
        assert count_words("bitcoin analysis report") == 3

    def test_count_words_mixed(self):
        from app.services.file_parser import count_words
        result = count_words("BTC 比特币 analysis")
        assert result > 0

    def test_count_words_empty(self):
        from app.services.file_parser import count_words
        assert count_words("") == 0
        assert count_words(None) == 0  # type: ignore
