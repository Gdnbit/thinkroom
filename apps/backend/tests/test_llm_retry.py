"""B2 · call_with_schema_retry 单测：验证重试、纠错注入、最终失败抛出"""
import json
import pytest
from pydantic import BaseModel, ValidationError

from app.utils.llm_retry import call_with_schema_retry, RetryableParseError


class _Demo(BaseModel):
    title: str
    score: float


def test_first_attempt_success():
    """一次就成功，不应触发重试"""
    calls = []

    def llm(sys_p, usr_p):
        calls.append(usr_p)
        return '{"title": "ok", "score": 0.8}'

    result = call_with_schema_retry(
        llm_call=llm,
        system_prompt="SYS",
        user_prompt="U",
        parser=lambda t: json.loads(t),
        validator=lambda d: _Demo(**d),
        context="test",
    )
    assert result.title == "ok"
    assert len(calls) == 1  # 只调一次


def test_retry_on_bad_json_then_success():
    """首次非法 JSON，重试成功"""
    responses = iter([
        "not json at all",
        '{"title": "recovered", "score": 0.7}',
    ])
    calls = []

    def llm(sys_p, usr_p):
        calls.append(usr_p)
        return next(responses)

    result = call_with_schema_retry(
        llm_call=llm,
        system_prompt="SYS",
        user_prompt="U",
        parser=json.loads,
        validator=lambda d: _Demo(**d),
        context="test",
    )
    assert result.title == "recovered"
    assert len(calls) == 2
    # 重试时 prompt 应被追加错误反馈
    assert "上次输出解析失败" in calls[1] or "Previous attempt failed" in calls[1]


def test_retry_on_validation_error_then_success():
    """首次字段类型错（score 不是数字），重试成功"""
    responses = iter([
        '{"title": "t", "score": "not-a-number"}',
        '{"title": "t", "score": 0.5}',
    ])

    def llm(sys_p, usr_p):
        return next(responses)

    result = call_with_schema_retry(
        llm_call=llm,
        system_prompt="SYS",
        user_prompt="U",
        parser=json.loads,
        validator=lambda d: _Demo(**d),
        context="test",
    )
    assert result.score == 0.5


def test_all_attempts_fail_raises_retryable():
    """两次都失败 → 抛 RetryableParseError"""
    def llm(sys_p, usr_p):
        return "garbage"

    with pytest.raises(RetryableParseError) as exc:
        call_with_schema_retry(
            llm_call=llm,
            system_prompt="SYS",
            user_prompt="U",
            parser=json.loads,
            validator=lambda d: _Demo(**d),
            context="test",
        )
    assert "all 2 attempts failed" in str(exc.value)


def test_custom_max_retries_zero_means_single_attempt():
    """max_retries=0 → 只试一次，失败立刻抛"""
    def llm(sys_p, usr_p):
        return "bad"

    with pytest.raises(RetryableParseError):
        call_with_schema_retry(
            llm_call=llm,
            system_prompt="SYS",
            user_prompt="U",
            parser=json.loads,
            validator=lambda d: _Demo(**d),
            context="test",
            max_retries=0,
        )


def test_parser_raising_value_error_triggers_retry():
    """parser 抛 ValueError（schema strict 校验失败）也应重试"""
    responses = iter([
        '{"title": "", "score": 0.5}',   # 空 title 被 parser 拒绝
        '{"title": "ok", "score": 0.5}',
    ])
    calls = []

    def llm(sys_p, usr_p):
        calls.append(usr_p)
        return next(responses)

    def strict_parse(text):
        d = json.loads(text)
        if not d.get("title"):
            raise ValueError("title empty")
        return d

    result = call_with_schema_retry(
        llm_call=llm,
        system_prompt="SYS",
        user_prompt="U",
        parser=strict_parse,
        validator=lambda d: _Demo(**d),
        context="test",
    )
    assert result.title == "ok"
    assert len(calls) == 2
