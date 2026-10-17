"""A2 验证：cost_scope 累计多次 LLM 调用的成本"""
from __future__ import annotations

from app.utils.cost_scope import cost_scope, CostScope, _active_scopes, _report_usage


class TestBasicAccumulation:
    def test_single_call_recorded(self):
        with cost_scope() as scope:
            _report_usage({
                "model": "test", "skill_role": "x",
                "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01,
            })
        assert scope.total_cost_usd == 0.01
        assert scope.total_input_tokens == 100
        assert scope.total_output_tokens == 50
        assert scope.call_count == 1

    def test_multiple_calls_accumulate(self):
        with cost_scope(tag="briefing") as scope:
            _report_usage({"cost_usd": 0.01, "input_tokens": 100, "output_tokens": 50, "model": "a"})
            _report_usage({"cost_usd": 0.02, "input_tokens": 200, "output_tokens": 80, "model": "b"})
            _report_usage({"cost_usd": 0.03, "input_tokens": 150, "output_tokens": 60, "model": "c"})
        assert scope.total_cost_usd == 0.06
        assert scope.total_input_tokens == 450
        assert scope.total_output_tokens == 190
        assert scope.call_count == 3
        assert scope.tag == "briefing"

    def test_outside_scope_noop(self):
        """scope 外 _report_usage 应安全 no-op"""
        _report_usage({"cost_usd": 0.99, "input_tokens": 1})
        # 没有断言 —— 只要不抛异常即可


class TestNestedScopes:
    def test_inner_bubbles_to_outer(self):
        """嵌套：内层调用应同时累计到外层"""
        with cost_scope(tag="outer") as outer:
            _report_usage({"cost_usd": 0.01, "input_tokens": 10, "output_tokens": 5, "model": "a"})
            with cost_scope(tag="inner") as inner:
                _report_usage({"cost_usd": 0.02, "input_tokens": 20, "output_tokens": 10, "model": "b"})
                _report_usage({"cost_usd": 0.03, "input_tokens": 30, "output_tokens": 15, "model": "c"})
            # 出内层后再记录一次
            _report_usage({"cost_usd": 0.04, "input_tokens": 40, "output_tokens": 20, "model": "d"})

        # 内层只含嵌套期间的 2 次
        assert inner.call_count == 2
        assert inner.total_cost_usd == 0.05

        # 外层含全部 4 次
        assert outer.call_count == 4
        assert round(outer.total_cost_usd, 6) == 0.10


class TestCleanup:
    def test_scope_stack_cleaned_after_exit(self):
        """退出 with 后，活跃栈应恢复为空"""
        assert _active_scopes.get() == []
        with cost_scope() as _:
            assert len(_active_scopes.get()) == 1
        assert _active_scopes.get() == []

    def test_exception_also_cleans_stack(self):
        try:
            with cost_scope() as _:
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert _active_scopes.get() == []


class TestMalformedInput:
    def test_missing_fields_handled(self):
        with cost_scope() as scope:
            _report_usage({})  # 全空
            _report_usage({"cost_usd": None, "input_tokens": None, "output_tokens": None})
        assert scope.total_cost_usd == 0.0
        assert scope.total_input_tokens == 0
        assert scope.call_count == 2

    def test_non_dict_ignored(self):
        with cost_scope() as scope:
            _report_usage("garbage")  # type: ignore
            _report_usage(None)  # type: ignore
        assert scope.call_count == 0

    def test_to_dict_format(self):
        with cost_scope(tag="x") as scope:
            _report_usage({"cost_usd": 0.05, "input_tokens": 100, "output_tokens": 50, "model": "m"})
        d = scope.to_dict()
        assert d["total_cost_usd"] == 0.05
        assert d["call_count"] == 1
        assert len(d["calls"]) == 1
        assert d["calls"][0]["model"] == "m"

    def test_trace_fields_preserved_in_calls(self):
        with cost_scope(tag="trace") as scope:
            _report_usage({
                "model": "model-a",
                "requested_model": "model-requested",
                "configured_fallback_models": ["model-b", "model-c"],
                "skill_role": "briefing",
                "input_tokens": 120,
                "output_tokens": 60,
                "cost_usd": 0.0123,
                "duration_ms": 345,
                "prompt_hash": "sha256:prompt",
                "response_hash": "sha256:response",
                "fallback_chain": ["model-a", "model-b"],
                "response_chars": 789,
                "mock": False,
                "generation_id": "gen-trace",
                "cost_source": "provider_usage",
            })

        call = scope.to_dict()["calls"][0]
        assert call["requested_model"] == "model-requested"
        assert call["configured_fallback_models"] == ["model-b", "model-c"]
        assert call["duration_ms"] == 345
        assert call["prompt_hash"] == "sha256:prompt"
        assert call["response_hash"] == "sha256:response"
        assert call["fallback_chain"] == ["model-a", "model-b"]
        assert call["response_chars"] == 789
        assert call["mock"] is False
        assert call["generation_id"] == "gen-trace"
        assert call["cost_source"] == "provider_usage"
