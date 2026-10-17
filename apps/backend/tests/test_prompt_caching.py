"""S3 验证：LLMClient._inject_prompt_caching 按 provider 正确注入缓存标记"""
from __future__ import annotations

from app.utils.llm_client import LLMClient


_LARGE_PROMPT = "你是专业的金融研究分析师。" * 200  # ~2400 字符，超过 2000 阈值


class TestAnthropicCaching:
    def test_system_content_becomes_array_with_cache_control(self):
        client = LLMClient()
        messages = [
            {"role": "system", "content": _LARGE_PROMPT},
            {"role": "user", "content": "BTC 怎么看"},
        ]
        new_msgs, extra = client._inject_prompt_caching(
            model="anthropic/claude-opus-4.6",
            messages=messages,
            system_prompt=_LARGE_PROMPT,
            skill_role="briefing",
        )
        # system content 应变成数组 + cache_control
        sys_msg = new_msgs[0]
        assert isinstance(sys_msg["content"], list)
        assert len(sys_msg["content"]) == 1
        assert sys_msg["content"][0]["type"] == "text"
        assert sys_msg["content"][0]["text"] == _LARGE_PROMPT
        assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}
        # user message 不改
        assert new_msgs[1]["content"] == "BTC 怎么看"
        # Anthropic 不需要 extra_body
        assert extra == {}

    def test_claude_variant_also_matched(self):
        client = LLMClient()
        new_msgs, _ = client._inject_prompt_caching(
            model="claude-sonnet-4.6",  # 简写形式
            messages=[{"role": "system", "content": _LARGE_PROMPT}],
            system_prompt=_LARGE_PROMPT,
        )
        assert isinstance(new_msgs[0]["content"], list)


class TestOpenAICaching:
    def test_prompt_cache_key_injected(self):
        client = LLMClient()
        messages = [{"role": "system", "content": _LARGE_PROMPT}]
        new_msgs, extra = client._inject_prompt_caching(
            model="openai/gpt-5.4",
            messages=messages,
            system_prompt=_LARGE_PROMPT,
            skill_role="briefing",
        )
        # messages 不变
        assert new_msgs == messages
        # extra_body 含 prompt_cache_key
        assert extra["prompt_cache_key"] == "skill:briefing"

    def test_default_cache_key_when_no_role(self):
        client = LLMClient()
        _, extra = client._inject_prompt_caching(
            model="openai/gpt-5.4",
            messages=[{"role": "system", "content": _LARGE_PROMPT}],
            system_prompt=_LARGE_PROMPT,
            skill_role=None,
        )
        assert extra["prompt_cache_key"] == "skill:default"


class TestGeminiAndOthers:
    def test_gemini_no_cache_markers(self):
        client = LLMClient()
        messages = [{"role": "system", "content": _LARGE_PROMPT}]
        new_msgs, extra = client._inject_prompt_caching(
            model="google/gemini-3.1-pro-preview",
            messages=messages,
            system_prompt=_LARGE_PROMPT,
        )
        assert new_msgs == messages
        assert extra == {}

    def test_deepseek_no_explicit_markers(self):
        """DeepSeek 自动缓存，无需标记"""
        client = LLMClient()
        _, extra = client._inject_prompt_caching(
            model="deepseek/deepseek-v3.2",
            messages=[{"role": "system", "content": _LARGE_PROMPT}],
            system_prompt=_LARGE_PROMPT,
        )
        assert extra == {}


class TestCachingBoundaries:
    def test_short_prompt_skipped(self):
        """小 system prompt（< 2000 字符）不缓存"""
        client = LLMClient()
        short = "Hello"
        messages = [{"role": "system", "content": short}]
        new_msgs, extra = client._inject_prompt_caching(
            model="anthropic/claude-opus-4.6",
            messages=messages,
            system_prompt=short,
        )
        # 不改 messages，不加 extra
        assert new_msgs == messages
        assert extra == {}

    def test_empty_prompt_skipped(self):
        client = LLMClient()
        new_msgs, extra = client._inject_prompt_caching(
            model="anthropic/claude-opus-4.6",
            messages=[],
            system_prompt="",
        )
        assert new_msgs == []
        assert extra == {}

    def test_disabled_via_settings(self, monkeypatch):
        """settings.ENABLE_PROMPT_CACHING=False 时完全跳过"""
        from app import config
        monkeypatch.setattr(config.settings, "ENABLE_PROMPT_CACHING", False)

        client = LLMClient()
        messages = [{"role": "system", "content": _LARGE_PROMPT}]
        new_msgs, extra = client._inject_prompt_caching(
            model="anthropic/claude-opus-4.6",
            messages=messages,
            system_prompt=_LARGE_PROMPT,
        )
        assert new_msgs == messages  # 不改
        assert extra == {}
