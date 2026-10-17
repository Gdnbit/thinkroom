"""LLM 客户端封装 — OpenRouter 统一 API（2026年4月优化版）

支持：
1. 模型 Fallback（主选失败自动切备选）
2. Reasoning Effort（Gemini Pro/Flash 的思维预算控制）
3. 结构化输出（JSON Schema 严格模式）
4. 批量 Embedding（降低延迟和成本）
5. 智能重试（指数退避，区分错误类型）
"""
import json
import time
import hashlib
import math
from contextvars import ContextVar
from typing import Optional, Tuple, List, Dict, Any
from loguru import logger
from app.config import settings


# OpenRouter 模型价格（USD/1M tokens）— 2026年4月
# 来源：各模型的 OpenRouter 模型页标价
_MODEL_PRICES: dict = {
    # ── 当前系统活跃模型（2026-04-10 审计后）──
    "anthropic/claude-opus-4.6": (5.0, 25.0),
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),
    "google/gemini-3.1-pro-preview": (2.0, 12.0),
    "google/gemini-3-pro": (1.25, 10.0),
    "google/gemini-3-flash-preview": (0.50, 3.0),
    "openai/gpt-5.4": (2.50, 15.0),
    "openai/gpt-5.4-mini": (0.75, 4.50),
    "openai/gpt-5.4-nano": (0.20, 1.25),
    "z-ai/glm-5": (0.72, 2.30),
    "xiaomi/mimo-v2-flash": (0.09, 0.29),
    "xiaomi/mimo-v2-pro": (1.0, 3.0),
    # ── Embedding ──
    "qwen/qwen3-embedding-4b": (0.01, 0.0),
    "google/gemini-embedding-001": (0.15, 0.0),
    "openai/text-embedding-3-small": (0.02, 0.0),
    # ── 旧模型/兼容（不再作为默认，仅记录价格供日志回溯）──
    "deepseek/deepseek-v3.2": (0.26, 0.38),
    "google/gemini-3.1-flash-lite-preview": (0.25, 1.50),
    "qwen/qwen3.5-9b": (0.05, 0.15),
    "qwen/qwen-turbo": (0.03, 0.10),
    "x-ai/grok-4.20": (3.0, 15.0),
}


class LLMProviderUnavailableError(RuntimeError):
    """Raised when a production LLM request cannot be served by a real provider."""

    def __init__(self, error: str, error_type: str):
        self.error = error
        self.error_type = error_type
        super().__init__(f"{error}:{error_type}")


class LLMClient:
    def __init__(self):
        self._openai = None
        self._openai_by_role = {}
        # The client is a process-wide singleton. Request-local trace state must
        # not leak between concurrent threads or asyncio tasks.
        self._last_usage_context: ContextVar[dict[str, Any] | None] = ContextVar(
            f"llm_last_usage_{id(self)}",
            default=None,
        )
        self._last_failure_type_context: ContextVar[str | None] = ContextVar(
            f"llm_last_failure_type_{id(self)}",
            default=None,
        )
        self._last_failure_message_context: ContextVar[str | None] = ContextVar(
            f"llm_last_failure_message_{id(self)}",
            default=None,
        )

    @property
    def _last_usage(self) -> dict[str, Any]:
        return self._last_usage_context.get() or {}

    @_last_usage.setter
    def _last_usage(self, value: dict[str, Any] | None) -> None:
        self._last_usage_context.set(dict(value or {}))

    @property
    def _last_failure_type(self) -> str | None:
        return self._last_failure_type_context.get()

    @_last_failure_type.setter
    def _last_failure_type(self, value: str | None) -> None:
        self._last_failure_type_context.set(value)

    @property
    def _last_failure_message(self) -> str | None:
        return self._last_failure_message_context.get()

    @_last_failure_message.setter
    def _last_failure_message(self, value: str | None) -> None:
        self._last_failure_message_context.set(value)

    def _get_model_by_role(self, role: Optional[str]) -> Tuple[str, Optional[str]]:
        """按角色选择对应模型，返回 (主选, 备选)

        自动从 role_registry 读取，新增模型角色时无需修改此处。
        """
        from app.utils.role_registry import get_role_model
        return get_role_model((role or "").lower())

    @staticmethod
    def _hash_text(text: str) -> str:
        return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _usage_value(usage: Any, key: str) -> Any:
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage.get(key)
        value = getattr(usage, key, None)
        if value is not None:
            return value
        model_extra = getattr(usage, "model_extra", None)
        if isinstance(model_extra, dict) and key in model_extra:
            return model_extra.get(key)
        model_dump = getattr(usage, "model_dump", None)
        if callable(model_dump):
            try:
                payload = model_dump()
                if isinstance(payload, dict):
                    return payload.get(key)
            except Exception:
                return None
        return None

    @classmethod
    def _provider_usage_cost(cls, usage: Any) -> Optional[float]:
        value = cls._usage_value(usage, "cost")
        if value is None:
            return None
        try:
            cost = float(value)
        except (TypeError, ValueError):
            return None
        return cost if math.isfinite(cost) and cost >= 0 else None

    @staticmethod
    def _model_prices(model: str) -> Optional[Tuple[float, float]]:
        normalized = (model or "").strip()
        if normalized in _MODEL_PRICES:
            return _MODEL_PRICES[normalized]
        for known_model in sorted(_MODEL_PRICES, key=len, reverse=True):
            prefix = f"{known_model}-"
            if not normalized.startswith(prefix):
                continue
            version = normalized[len(prefix):].replace("-", "")
            if version.startswith("20") and len(version) == 8 and version.isdigit():
                return _MODEL_PRICES[known_model]
        return None

    def _classify_error(self, error: Any, status_code: int | None = None) -> str:
        """Normalize provider/client failures for trace and UI handling."""
        code = status_code if status_code is not None else getattr(error, "status_code", None)
        if code == 401:
            return "auth_error"
        if code == 402:
            return "billing_error"
        if code == 403:
            return "permission_or_region"
        if code == 408:
            return "timeout"
        if code == 429:
            return "rate_limited"
        if code in (500, 502, 503, 504):
            return "upstream_unavailable"
        if code in (400, 404, 422):
            return "bad_request"

        message = str(error or "").lower()
        if "api key" in message or "unauthorized" in message or "401" in message:
            return "auth_error"
        if "insufficient" in message or "balance" in message or "quota" in message or "402" in message:
            return "billing_error"
        if "rate limit" in message or "too many request" in message or "429" in message:
            return "rate_limited"
        if "timeout" in message or "timed out" in message or "read timeout" in message:
            return "timeout"
        if "proxy" in message or "connect" in message or "network" in message or "connection" in message:
            return "network_error"
        if "empty" in message:
            return "empty_response"
        return "unknown_error"

    def _remember_failure(self, error: Any, status_code: int | None = None) -> str:
        error_type = self._classify_error(error, status_code)
        self._last_failure_type = error_type
        self._last_failure_message = str(error or "")[:300]
        return error_type

    @staticmethod
    def _unique_models(models: list[str | None], exclude: set[str] | None = None) -> list[str]:
        excluded = exclude or set()
        seen: set[str] = set()
        result: list[str] = []
        for model in models:
            value = (model or "").strip()
            if not value or value in excluded or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _is_production(self) -> bool:
        return (settings.APP_ENV or "").lower() in {"production", "prod"}

    def _raise_provider_failure(
        self,
        *,
        started_at: float,
        system_prompt: str,
        user_prompt: str,
        model: str,
        skill_role: Optional[str],
        fallback_chain: list[str],
        error: str,
        error_type: str,
        configured_fallback_models: list[str] | None = None,
    ) -> None:
        self._last_usage = {
            "model": model,
            "skill_role": skill_role,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }
        if configured_fallback_models is not None:
            self._last_usage["configured_fallback_models"] = configured_fallback_models
        self._finalize_usage_trace(
            started_at=started_at,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_text="",
            fallback_chain=fallback_chain,
            mock=False,
            error=error,
            error_type=error_type,
        )
        raise LLMProviderUnavailableError(error, error_type)

    def _finalize_usage_trace(
        self,
        *,
        started_at: float,
        system_prompt: str,
        user_prompt: str,
        response_text: str,
        fallback_chain: list[str],
        mock: bool,
        error: str | None = None,
        error_type: str | None = None,
        configured_fallback_models: list[str] | None = None,
    ) -> None:
        """Attach trace metadata to _last_usage and report it to active cost scopes."""
        usage = dict(self._last_usage or {})
        usage.setdefault("input_tokens", 0)
        usage.setdefault("output_tokens", 0)
        usage.setdefault("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        usage.setdefault("cost_usd", 0.0)
        usage["duration_ms"] = int((time.perf_counter() - started_at) * 1000)
        usage["prompt_hash"] = self._hash_text(f"{system_prompt}\n---\n{user_prompt}")
        usage["response_hash"] = self._hash_text(response_text)
        usage["response_chars"] = len(response_text or "")
        usage["fallback_chain"] = [m for m in fallback_chain if m]
        usage["mock"] = mock
        if configured_fallback_models is not None:
            usage["configured_fallback_models"] = configured_fallback_models
        if error:
            usage["error"] = error[:300]
            usage["error_type"] = error_type or self._classify_error(error)
        elif error_type:
            usage["error_type"] = error_type
        self._last_usage = usage
        try:
            from app.utils.cost_scope import _report_usage
            _report_usage(self._last_usage)
        except Exception:
            pass

    def _get_openai_client(self, role: Optional[str] = None):
        """获取 OpenAI 兼容客户端（连接到 OpenRouter）"""
        role = (role or "global").lower()
        if role in self._openai_by_role:
            return self._openai_by_role[role]

        try:
            import openai
            import httpx

            api_key = settings.OPENROUTER_API_KEY
            if not api_key or api_key in ("", "your_openrouter_api_key"):
                logger.warning("[LLM] OPENROUTER_API_KEY not configured")
                return None

            http_client_kwargs = {"timeout": settings.OPENROUTER_TIMEOUT_SECONDS}
            if settings.HTTP_PROXY or settings.HTTPS_PROXY:
                proxy = settings.HTTP_PROXY or settings.HTTPS_PROXY
                http_client_kwargs["proxy"] = proxy
                logger.debug(f"[LLM] Using proxy: {proxy}")
            else:
                http_client_kwargs["proxy"] = None

            kwargs = {
                "api_key": api_key,
                "base_url": settings.OPENROUTER_BASE_URL,
                "http_client": httpx.Client(**http_client_kwargs),
                "timeout": settings.OPENROUTER_TIMEOUT_SECONDS,
                "default_headers": {
                    "HTTP-Referer": settings.OPENROUTER_HTTP_REFERER,
                    "X-Title": settings.OPENROUTER_APP_TITLE,
                },
            }

            client = openai.OpenAI(**kwargs)
            self._openai_by_role[role] = client
            if role == "global":
                self._openai = client
            logger.debug(f"[LLM] OpenRouter client initialized (role={role})")
            return client
        except Exception as e:
            logger.warning(f"[LLM] OpenRouter client init failed (role={role}): {e}")
            return None

    def _should_retry(self, status_code: int) -> bool:
        """判断是否应该重试"""
        # 408/429/500/502/503 做指数退避重试
        return status_code in (408, 429, 500, 502, 503)

    def _call_with_retry(
        self,
        client,
        model: str,
        messages: List[Dict],
        **kwargs
    ) -> Optional[Any]:
        """带重试的 API 调用"""
        retry_count = 0
        base_delay = settings.OPENROUTER_RETRY_BASE_MS / 1000.0

        while retry_count <= settings.OPENROUTER_MAX_RETRIES:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs
                )
                return response
            except Exception as e:
                error_code = getattr(e, "status_code", None)
                error_type = self._remember_failure(e, error_code)
                
                # 区域限制（403）— 不重试，快速返回让上层 fallback
                if error_code == 403:
                    logger.error(f"[LLM] Region/permission blocked (403, {error_type}): {e}")
                    return None

                # 不可重试的错误
                if error_code in (400, 401, 404, 422):
                    logger.error(f"[LLM] Non-retryable error ({error_code}, {error_type}): {e}")
                    return None
                
                # 余额不足
                if error_code == 402:
                    logger.error(f"[LLM] Insufficient balance (402, {error_type}): {e}")
                    return None
                
                # 可重试的错误
                if self._should_retry(error_code) and retry_count < settings.OPENROUTER_MAX_RETRIES:
                    delay = min(
                        base_delay * (2 ** retry_count),
                        settings.OPENROUTER_RETRY_MAX_MS / 1000.0
                    )
                    logger.warning(f"[LLM] Retrying after {delay:.2f}s (attempt {retry_count + 1}/{settings.OPENROUTER_MAX_RETRIES}, {error_type}): {e}")
                    time.sleep(delay)
                    retry_count += 1
                else:
                    logger.error(f"[LLM] API call failed ({error_type}): {e}")
                    return None

        return None

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        skill_role: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        response_format: Optional[Dict] = None,
    ) -> str:
        """调用 LLM，返回文本响应"""
        text, _ = self.complete_with_role(
            skill_role, system_prompt, user_prompt, max_tokens, temperature,
            model, reasoning_effort, response_format
        )
        return text

    def complete_with_role(
        self,
        skill_role: Optional[str],
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        response_format: Optional[Dict] = None,
    ) -> Tuple[str, str]:
        """按 Skill 角色选择对应模型，支持 Fallback"""
        primary_model, fallback_model = self._get_model_by_role(skill_role)
        model = model or primary_model
        requested_model = model
        emergency_model = getattr(settings, 'REGION_FALLBACK_MODEL', 'deepseek/deepseek-v3.2')
        configured_fallback_models = (
            self._unique_models([fallback_model, emergency_model], exclude={model})
            if settings.OPENROUTER_ENABLE_MODEL_FALLBACKS
            else []
        )
        call_started_at = time.perf_counter()
        fallback_chain: list[str] = [model] if model else []
        self._last_failure_type = None
        self._last_failure_message = None

        # ── P2-D: 显式 OPENROUTER_MOCK 开关（locust 压测用） ──
        # 早于 _get_openai_client：即使 API key 配置正常，也强制走 mock 路径。
        # 与「API key 缺失自动 mock」（client=None 那条路径）独立。
        if getattr(settings, "OPENROUTER_MOCK", False):
            text = self._mock_response_skill_aware(skill_role, model)
            self._finalize_usage_trace(
                started_at=call_started_at,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_text=text,
                fallback_chain=fallback_chain,
                mock=True,
            )
            return text, model

        client = self._get_openai_client(skill_role)
        if not client:
            error_type = "missing_api_key" if not (settings.OPENROUTER_API_KEY or "").strip() else "client_unavailable"
            if self._is_production():
                logger.error(f"[LLM] No OpenRouter client in production, failing closed ({error_type})")
                self._raise_provider_failure(
                    started_at=call_started_at,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    skill_role=skill_role,
                    fallback_chain=fallback_chain,
                    error="openrouter_client_unavailable",
                    error_type=error_type,
                    configured_fallback_models=configured_fallback_models,
                )
            logger.warning(f"[LLM] No OpenRouter client, returning skill-aware mock response")
            text = self._mock_response_skill_aware(skill_role, model)
            self._finalize_usage_trace(
                started_at=call_started_at,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_text=text,
                fallback_chain=fallback_chain,
                mock=True,
                error="openrouter_client_unavailable",
                error_type=error_type,
                configured_fallback_models=configured_fallback_models,
            )
            return text, model

        # 构建请求参数
        kwargs = {
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # ── OpenAI gpt-5.4 系列 min max_output_tokens 保护 ──
        _model_lower = (model or "").lower()
        if "gpt-5" in _model_lower or "o1" in _model_lower or "o3" in _model_lower:
            _MIN_OPENAI_TOKENS = 128
            if kwargs["max_tokens"] < _MIN_OPENAI_TOKENS:
                kwargs["max_tokens"] = _MIN_OPENAI_TOKENS

        # ── 思考模型保护：检测隐式 thinking 模型，自动补偿 token 预算 ──
        # qwen3.5-*、qwq-* 等模型默认开启 thinking mode，reasoning tokens 会消耗
        # max_tokens 预算，导致实际 content 为空。对于这类模型：
        # 1. 对结构化输出任务，提高 max_tokens 给 thinking 留余量
        # 2. 或者设置 reasoning={"effort":"off"} 关闭 thinking（更节省 token）
        _model_lower = model.lower()
        _THINKING_MODELS = ("qwen3.5", "qwq", "qwen3-", "mimo")  # 已知支持 thinking 的系列
        _is_thinking_model = any(tag in _model_lower for tag in _THINKING_MODELS)
        if _is_thinking_model and not reasoning_effort:
            # 对结构化 JSON 输出任务，关闭 thinking 模式（更可靠）
            _structured_roles = {"extraction", "speaker", "date_extraction",
                                 "fusion", "intent", "strategy"}
            if skill_role and any(r in skill_role for r in _structured_roles):
                kwargs["reasoning"] = {"effort": "off"}
                logger.debug(f"[LLM] Thinking model {model} + structured role {skill_role}: "
                             f"disabled reasoning to prevent empty content")
            else:
                # 非结构化任务（briefing/committee/adjudication），给 thinking 留 budget
                _thinking_budget = max(max_tokens, 4096)
                if _thinking_budget > max_tokens:
                    kwargs["max_tokens"] = _thinking_budget
                    logger.debug(f"[LLM] Thinking model {model}: bumped max_tokens "
                                 f"{max_tokens} → {_thinking_budget}")

        # 添加 Reasoning Effort（Gemini Pro/Flash）
        if reasoning_effort and "gemini" in model.lower():
            kwargs["reasoning"] = {"effort": reasoning_effort}

        # 添加结构化输出
        if response_format and settings.OPENROUTER_USE_JSON_SCHEMA:
            kwargs["response_format"] = response_format

        # 添加 Service Tier 和 Provider Sort
        if settings.OPENROUTER_SERVICE_TIER != "auto":
            kwargs["service_tier"] = settings.OPENROUTER_SERVICE_TIER

        # 模型自适应 Prompt 优化
        from app.utils.prompt_adapter import adapt_prompt, adapt_temperature
        adapted_system = adapt_prompt(system_prompt, model, skill_role)
        kwargs["temperature"] = adapt_temperature(temperature, model, skill_role)

        messages = [
            {"role": "system", "content": adapted_system},
            {"role": "user", "content": user_prompt},
        ]

        # ── S3: Prompt Caching —— 对大 system prompt 注入缓存标记 ──
        # Anthropic:  content 数组 + cache_control: {"type": "ephemeral"}
        # OpenAI:     prompt_cache_key 传入 extra_body（prefix 匹配自动 cache）
        # DeepSeek:   自动缓存，无需额外参数
        # Gemini/GLM/其他：目前 OpenRouter 无稳定缓存支持，跳过
        messages, extra_body = self._inject_prompt_caching(model, messages, adapted_system, skill_role)
        if extra_body:
            kwargs.setdefault("extra_body", {}).update(extra_body)

        openrouter_extra = kwargs.setdefault("extra_body", {})
        if configured_fallback_models:
            openrouter_extra["models"] = configured_fallback_models
        provider_sort = (getattr(settings, "OPENROUTER_PROVIDER_SORT", "") or "").strip()
        if provider_sort and provider_sort.lower() != "auto":
            provider_options = openrouter_extra.setdefault("provider", {})
            if isinstance(provider_options, dict):
                provider_options.setdefault("sort", provider_sort)

        # 尝试主选模型
        response = self._call_with_retry(client, model, messages, **kwargs)
        _transport_failed = response is None

        # 检测空响应 / 截断响应：触发 fallback
        # 1. 空内容：thinking-mode（tokens > 0）或静默失败（tokens = 0）
        # 2. 截断：output_tokens >= max_tokens 且 max_tokens 足够大（>=500）
        #    - 小 max_tokens（如 model-ping 的 20）不做截断判断，因为多数模型不严格遵守小限制
        #    - 大 max_tokens（如 extraction 的 3000）则 reasoning 可能消耗完预算导致 JSON 不完整
        _needs_fallback = not response
        _max_tok = kwargs.get("max_tokens", 4096)
        _TRUNCATION_MIN_TOKENS = 500
        if response and not _needs_fallback:
            try:
                _primary_text = (response.choices[0].message.content or "").strip()
                _out_tokens = (response.usage.completion_tokens or 0) if response.usage else 0
                if not _primary_text:
                    _reason = "thinking-mode" if _out_tokens > 0 else "silent-empty"
                    logger.warning(
                        f"[LLM] {model} returned empty content "
                        f"({_reason}, {_out_tokens} output tokens), triggering fallback"
                    )
                    _needs_fallback = True
                elif _max_tok >= _TRUNCATION_MIN_TOKENS and _out_tokens >= _max_tok:
                    logger.warning(
                        f"[LLM] {model} hit max_tokens ({_out_tokens}/{_max_tok}), "
                        f"response likely truncated ({len(_primary_text)} chars), triggering fallback"
                    )
                    _needs_fallback = True
            except Exception:
                pass
        
        # 如果主选失败（或空响应）且启用 Fallback，尝试备选
        _native_fallback_already_attempted = _transport_failed and bool(configured_fallback_models)
        if (
            _needs_fallback
            and not _native_fallback_already_attempted
            and fallback_model
            and settings.OPENROUTER_ENABLE_MODEL_FALLBACKS
        ):
            logger.info(f"[LLM] Primary model {model} failed, trying fallback {fallback_model}")
            if fallback_model not in fallback_chain:
                fallback_chain.append(fallback_model)
            response = self._call_with_retry(client, fallback_model, messages, **kwargs)
            model = fallback_model
            # 备选也可能空响应或截断（均触发万能兜底）
            if response:
                try:
                    _fb_text = (response.choices[0].message.content or "").strip()
                    _fb_tokens = (response.usage.completion_tokens or 0) if response.usage else 0
                    if not _fb_text:
                        _reason = "thinking-mode" if _fb_tokens > 0 else "silent-empty"
                        logger.warning(f"[LLM] Fallback {model} also returned empty content ({_reason}, {_fb_tokens} tokens)")
                        response = None  # 让万能兜底接管
                    elif _max_tok >= _TRUNCATION_MIN_TOKENS and _fb_tokens >= _max_tok:
                        logger.warning(
                            f"[LLM] Fallback {model} hit max_tokens ({_fb_tokens}/{_max_tok}), "
                            f"response likely truncated ({len(_fb_text)} chars), trying emergency"
                        )
                        response = None  # 让万能兜底接管
                except Exception:
                    pass

        # 万能兜底：主选和备选都失败时，使用跨厂商万能 fallback（通常为不受区域限制的模型）
        if not response and not _native_fallback_already_attempted and settings.OPENROUTER_ENABLE_MODEL_FALLBACKS:
            emergency = emergency_model
            if emergency and emergency not in (primary_model, fallback_model):
                logger.warning(f"[LLM] Both primary+fallback failed, trying emergency fallback {emergency}")
                if emergency not in fallback_chain:
                    fallback_chain.append(emergency)
                # 移除可能与万能模型不兼容的参数
                safe_kwargs = {k: v for k, v in kwargs.items() if k not in ('reasoning', 'service_tier')}
                response = self._call_with_retry(client, emergency, messages, **safe_kwargs)
                model = emergency

        if not response:
            if self._is_production():
                self._raise_provider_failure(
                    started_at=call_started_at,
                    system_prompt=adapted_system,
                    user_prompt=user_prompt,
                    model=model,
                    skill_role=skill_role,
                    fallback_chain=fallback_chain,
                    error="all_models_failed",
                    error_type=self._last_failure_type or "upstream_unavailable",
                    configured_fallback_models=configured_fallback_models,
                )
            text = self._mock_response_skill_aware(skill_role, model)
            self._finalize_usage_trace(
                started_at=call_started_at,
                system_prompt=adapted_system,
                user_prompt=user_prompt,
                response_text=text,
                fallback_chain=fallback_chain,
                mock=True,
                error="all_models_failed",
                error_type=self._last_failure_type or "upstream_unavailable",
                configured_fallback_models=configured_fallback_models,
            )
            return text, model

        try:
            actual_model = getattr(response, "model", None) or model
            if actual_model != model and actual_model not in fallback_chain:
                fallback_chain.append(actual_model)
            model = actual_model
            text = response.choices[0].message.content or ""
            if response.usage:
                input_tokens = response.usage.prompt_tokens or 0
                output_tokens = response.usage.completion_tokens or 0
                provider_cost = self._provider_usage_cost(response.usage)
                model_prices = self._model_prices(model)
                if provider_cost is not None:
                    cost = provider_cost
                    cost_source = "provider_usage"
                elif model_prices is not None:
                    cost = self.estimate_cost(model, input_tokens, output_tokens)
                    cost_source = "static_estimate"
                else:
                    cost = 0.0
                    cost_source = "unavailable"
                self._last_usage = {
                    "model": model,
                    "requested_model": requested_model,
                    "configured_fallback_models": configured_fallback_models,
                    "skill_role": skill_role,
                    "generation_id": getattr(response, "id", None),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "cost_usd": cost,
                    "cost_source": cost_source,
                }
                # 成本追踪
                try:
                    from app.services.cost_tracker import cost_tracker
                    cost_tracker.record(
                        model=model,
                        skill_role=skill_role or "unknown",
                        input_tokens=response.usage.prompt_tokens or 0,
                        output_tokens=response.usage.completion_tokens or 0,
                        total_tokens=response.usage.total_tokens or 0,
                        cost_usd=cost,
                    )
                except Exception:
                    pass  # 成本追踪失败不影响主流程
                # A2/P0.3: 冒泡到活跃 cost_scope（若存在），同时携带 trace metadata。
                self._finalize_usage_trace(
                    started_at=call_started_at,
                    system_prompt=adapted_system,
                    user_prompt=user_prompt,
                    response_text=text,
                    fallback_chain=fallback_chain,
                    mock=False,
                    configured_fallback_models=configured_fallback_models,
                )
            else:
                self._last_usage = {
                    "model": model,
                    "requested_model": requested_model,
                    "configured_fallback_models": configured_fallback_models,
                    "skill_role": skill_role,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
                self._finalize_usage_trace(
                    started_at=call_started_at,
                    system_prompt=adapted_system,
                    user_prompt=user_prompt,
                    response_text=text,
                    fallback_chain=fallback_chain,
                    mock=False,
                    configured_fallback_models=configured_fallback_models,
                )
            logger.debug(f"[LLM] {model} completed: {len(text)} chars, usage={self._last_usage}")
            return text, model
        except Exception as e:
            logger.error(f"[LLM] Response parsing failed: {e}")
            if self._is_production():
                self._raise_provider_failure(
                    started_at=call_started_at,
                    system_prompt=adapted_system if "adapted_system" in locals() else system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    skill_role=skill_role,
                    fallback_chain=fallback_chain,
                    error=f"response_parsing_failed:{e}",
                    error_type="response_parse_error",
                    configured_fallback_models=configured_fallback_models,
                )
            text = self._mock_response_skill_aware(skill_role, model)
            self._finalize_usage_trace(
                started_at=call_started_at,
                system_prompt=adapted_system if "adapted_system" in locals() else system_prompt,
                user_prompt=user_prompt,
                response_text=text,
                fallback_chain=fallback_chain,
                mock=True,
                error=f"response_parsing_failed:{e}",
                error_type="response_parse_error",
                configured_fallback_models=configured_fallback_models,
            )
            return text, model

    def complete_with_role_str(
        self,
        skill_role: Optional[str],
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        response_format: Optional[Dict] = None,
    ) -> str:
        """Backward-compatible string-only wrapper for legacy callsites."""
        text, _ = self.complete_with_role(
            skill_role=skill_role,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
        )
        return text

    def embed(
        self,
        texts: List[str] | str,
        model: Optional[str] = None,
    ) -> Optional[List[List[float]]]:
        """生成文本 embedding 向量（支持批量）"""
        if isinstance(texts, str):
            texts = [texts]

        model = model or settings.EMBEDDING_MODEL

        # ── P2-D: OPENROUTER_MOCK 开关（locust 压测用） ──
        # 返回确定性 mock embedding（基于文本 hash），保证向量化路径走通：
        #   - 维度 = settings.EMBEDDING_DIM（默认 2560）
        #   - 同输入文本 → 同向量（便于排查向量写入问题）
        #   - 已 normalize 到单位长度（cosine 距离合理）
        if getattr(settings, "OPENROUTER_MOCK", False):
            return [self._mock_embedding(t) for t in texts]

        client = self._get_openai_client("embedding")

        if not client:
            logger.warning(f"[LLM] No OpenRouter client for embedding")
            return None

        # Embeddings 使用专用 API（非 chat.completions）
        last_err = None
        for attempt in range(1, settings.OPENROUTER_MAX_RETRIES + 2):
            try:
                response = client.embeddings.create(
                    model=model,
                    input=[t[:8000] for t in texts],
                )
                if not response or not response.data:
                    raise ValueError("No embedding data received")
                
                # 按原始顺序返回向量
                embeddings = [None] * len(texts)
                for item in response.data:
                    embeddings[item.index] = item.embedding
                
                logger.debug(f"[LLM] Embedding generated: {len(embeddings)} vectors, {len(embeddings[0]) if embeddings[0] else 0} dims")
                return embeddings
            except Exception as e:
                last_err = e
                error_code = getattr(e, "status_code", None)
                if error_code and not self._should_retry(error_code):
                    break
                if attempt <= settings.OPENROUTER_MAX_RETRIES:
                    delay = min(
                        (settings.OPENROUTER_RETRY_BASE_MS / 1000.0) * (2 ** (attempt - 1)),
                        settings.OPENROUTER_RETRY_MAX_MS / 1000.0,
                    )
                    logger.warning(f"[LLM] Embedding retry {attempt}: {e}")
                    time.sleep(delay)

        # 主模型失败，尝试备选 embedding 模型
        fallback = getattr(settings, "EMBEDDING_FALLBACK_MODEL", None)
        if fallback and fallback != model:
            logger.info(f"[LLM] Embedding primary {model} failed, trying fallback {fallback}")
            try:
                response = client.embeddings.create(
                    model=fallback,
                    input=[t[:8000] for t in texts],
                )
                if response and response.data:
                    embeddings = [None] * len(texts)
                    for item in response.data:
                        embeddings[item.index] = item.embedding
                    # 维度校验：fallback 模型可能输出不同维度，防止写入 vec 存储时出错
                    expected_dim = getattr(settings, "EMBEDDING_DIM", 0)
                    actual_dim = len(embeddings[0]) if embeddings and embeddings[0] else 0
                    if expected_dim and actual_dim and actual_dim != expected_dim:
                        logger.warning(
                            f"[LLM] Embedding fallback dim mismatch: "
                            f"expected={expected_dim}, actual={actual_dim} (model={fallback}). "
                            f"Discarding to prevent vec store corruption."
                        )
                    else:
                        logger.info(f"[LLM] Embedding fallback {fallback} succeeded: {len(embeddings)} vectors")
                        return embeddings
            except Exception as e:
                logger.warning(f"[LLM] Embedding fallback {fallback} also failed: {e}")

        logger.error(f"[LLM] Embedding failed (model={model}): {last_err}")
        return None

    def ping_model(self, role: str, timeout_s: int = 15) -> dict:
        """轻量级模型连通性测试：短超时、不重试、不 fallback。

        返回 dict: {success, role, model, response, latency_ms, error}
        """
        import httpx as _httpx

        primary_model, _ = self._get_model_by_role(role)
        model = primary_model
        t0 = time.time()

        # ── P2-D: OPENROUTER_MOCK 开关 ──
        # 让 health/ready/system-doctor 等运维端点在压测中也走通
        if getattr(settings, "OPENROUTER_MOCK", False):
            self._maybe_sleep_mock_latency()
            ms = int((time.time() - t0) * 1000)
            return {"success": True, "role": role, "model": model,
                    "response": "OK", "latency_ms": ms, "error": None}

        try:
            api_key = settings.OPENROUTER_API_KEY
            if not api_key or api_key in ("", "your_openrouter_api_key"):
                return {"success": False, "role": role, "model": model,
                        "response": "", "latency_ms": 0, "error": "API key not configured"}

            # 构建独立的短超时 HTTP 客户端（不复用长连接池）
            http_kwargs = {"timeout": _httpx.Timeout(timeout_s, connect=10)}
            if settings.HTTP_PROXY or settings.HTTPS_PROXY:
                http_kwargs["proxy"] = settings.HTTP_PROXY or settings.HTTPS_PROXY

            with _httpx.Client(**http_kwargs) as http_client:
                import openai as _openai
                client = _openai.OpenAI(
                    api_key=api_key,
                    base_url=settings.OPENROUTER_BASE_URL,
                    http_client=http_client,
                    timeout=timeout_s,
                    default_headers={
                        "HTTP-Referer": settings.OPENROUTER_HTTP_REFERER,
                        "X-Title": settings.OPENROUTER_APP_TITLE,
                    },
                )
                # OpenAI gpt-5.4 系列要求 max_output_tokens >= 100
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "Reply with exactly: OK"},
                        {"role": "user", "content": "ping"},
                    ],
                    max_tokens=128,
                    temperature=0.0,
                )
                text = (resp.choices[0].message.content or "").strip() if resp and resp.choices else ""
                ms = int((time.time() - t0) * 1000)
                return {"success": bool(text), "role": role, "model": model,
                        "response": text[:50], "latency_ms": ms, "error": None}
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            err = str(e)[:200]
            # 识别常见错误模式
            if "proxy" in err.lower() or "connect" in err.lower():
                err = f"[网络/代理] {err}"
            elif "401" in err or "403" in err:
                err = f"[认证/区域限制] {err}"
            elif "timeout" in err.lower():
                err = f"[超时 {timeout_s}s] {err}"
            return {"success": False, "role": role, "model": model,
                    "response": "", "latency_ms": ms, "error": err}

    # ── S3: Prompt Caching 助手 ──────────────────────────────
    # 最小可缓存 token 数：Anthropic 要求 >= 1024（~3000 字符）
    # 小于此值的 system prompt 注入 cache_control 无意义，跳过避免额外开销
    _MIN_CACHEABLE_CHARS = 2000

    def _inject_prompt_caching(
        self,
        model: str,
        messages: List[Dict],
        system_prompt: str,
        skill_role: Optional[str] = None,
    ) -> Tuple[List[Dict], Dict[str, Any]]:
        """按 provider 在 messages / extra_body 注入 prompt caching 标记。

        Returns:
            (messages, extra_body)  —— messages 可能被改写（Anthropic 用 content 数组）；
                                     extra_body 是附加到 kwargs 的参数（OpenAI prompt_cache_key）
        """
        # 全局开关：默认启用，settings 层可禁用（调试/降级用）
        if not getattr(settings, "ENABLE_PROMPT_CACHING", True):
            return messages, {}

        # 小 prompt 不缓存（Anthropic 官方下限 1024 tokens ≈ 2000 汉字）
        if not system_prompt or len(system_prompt) < self._MIN_CACHEABLE_CHARS:
            return messages, {}

        model_lower = (model or "").lower()
        extra_body: Dict[str, Any] = {}

        # ── Anthropic（claude）：在 system content 注入 cache_control ephemeral ──
        if "anthropic" in model_lower or "claude" in model_lower:
            # 把 system message 的 content 从 str 改为 [{"type":"text", "text":..., "cache_control":{"type":"ephemeral"}}]
            new_messages = []
            for m in messages:
                if m.get("role") == "system" and isinstance(m.get("content"), str):
                    new_messages.append({
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": m["content"],
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    })
                else:
                    new_messages.append(m)
            logger.debug(f"[LLM] Anthropic cache_control injected (system={len(system_prompt)} chars)")
            return new_messages, extra_body

        # ── OpenAI（gpt-*）：prompt_cache_key 稳定 cache 路由 ──
        # OpenAI 对 >1024 token prefix 自动 cache，prompt_cache_key 让同一 skill 复用同一 cache
        if "openai" in model_lower or "gpt-" in model_lower or "o1" in model_lower or "o3" in model_lower:
            cache_key = f"skill:{skill_role or 'default'}"
            extra_body["prompt_cache_key"] = cache_key
            logger.debug(f"[LLM] OpenAI prompt_cache_key={cache_key} injected")
            return messages, extra_body

        # ── DeepSeek：自动缓存，无需配置 ──
        # Gemini / GLM / Qwen / 其他：OpenRouter 目前无稳定缓存支持，跳过
        return messages, extra_body

    def _mock_response(self, model: str) -> str:
        """生成 Mock 响应（无 API Key 时）"""
        return json.dumps({
            "mock": True,
            "model": model,
            "message": "Mock response (no API key configured)",
        }, ensure_ascii=False)

    # ── P2-D LLM Mock 模式（OPENROUTER_MOCK=1）──────────────────────────
    # 与 `_mock_response()` 区别：本组方法是显式开关触发的、按 skill_role
    # 返回更贴近真实结构的内容，让 chat / briefing / extraction 等下游
    # 解析路径都能跑通而不是 fallback 报错。
    #
    # 设计原则：
    #   1. 确定性：同 (skill_role, model) 输入 → 同输出（便于测试）
    #   2. 模拟延迟：可选 sleep OPENROUTER_MOCK_LATENCY_MS（让压测看到合理 P95）
    #   3. 结构兼容：JSON 类 skill 返回合法 JSON；纯文本类 skill 返回叙述
    #   4. 成本透传：mock 调用也写入 _last_usage（cost=0），下游 cost_scope 不会爆

    def _maybe_sleep_mock_latency(self) -> None:
        """模拟一段网络延迟（让 mock 模式下的端到端 P95 不至于 < 1ms）。"""
        ms = getattr(settings, "OPENROUTER_MOCK_LATENCY_MS", 0)
        if ms and ms > 0:
            time.sleep(ms / 1000.0)

    def _mock_response_skill_aware(self, skill_role: Optional[str], model: str) -> str:
        """按 skill_role 返回结构化 mock 响应（P2-D 压测专用）。

        覆盖项目当前所有 skill_role；未匹配的 role 返回纯文本兜底。
        """
        self._maybe_sleep_mock_latency()
        # 同时记录 mock usage，让 cost_scope/A2 累计逻辑不为空
        self._last_usage = {
            "model": model,
            "skill_role": skill_role,
            "input_tokens": 100,
            "output_tokens": 80,
            "total_tokens": 180,
            "cost_usd": 0.0,
            "mock": True,
        }

        role = (skill_role or "").lower()

        # ── JSON 结构类 ─────────────────────────────────────
        # extraction / claim 提取：返回 ClaimExtractionSkill 解析得了的格式
        if "extraction" in role or "claim" in role:
            return json.dumps({
                "_mock": True,
                "claims": [
                    {
                        "summary": "Mock claim: BTC short-term consolidation",
                        "stance": "neutral",
                        "confidence": 0.6,
                        "time_horizon": "short",
                        "asset": "BTC",
                        "full_quote": "Mock quote text for ground truth.",
                        "speaker": "MockSpeaker",
                        "_mock": True,
                    }
                ]
            }, ensure_ascii=False)

        # speaker / 来源画像：返回 SpeakerProfileSkill 期望的字段
        if "speaker" in role:
            return json.dumps({
                "speaker_id": "mock_speaker",
                "credibility": 0.7,
                "stance_bias": "neutral",
                "expertise_areas": ["crypto"],
                "_mock": True,
            }, ensure_ascii=False)

        # date_extraction：返回标准化日期
        if "date" in role:
            return json.dumps({
                "publish_date": "2026-04-25",
                "confidence": 0.8,
                "_mock": True,
            }, ensure_ascii=False)

        # intent / 意图识别：返回 IntentRecognitionSkill 期望的结构
        if "intent" in role:
            return json.dumps({
                "mode": "research",
                "assets": ["BTC"],
                "themes": [],
                "confidence": 0.85,
                "_mock": True,
            }, ensure_ascii=False)

        # query_rewrite：返回扩展后的检索查询
        if "rewrite" in role or "query" in role:
            return "BTC 价格走势 技术指标 市场情绪 综合分析（mock）"

        # fusion / 因果融合 / chat（两者都吃 fusion 角色）：
        # 包含 synthesis_narrative，让 chat.py 检测到 _mock 后正确解包
        if "fusion" in role or "synthesis" in role:
            return json.dumps({
                "synthesis_narrative": "（Mock 回答）BTC 当前处于震荡区间，技术面与情绪面短期中性。完整数据需配置 OPENROUTER_API_KEY 后获取。",
                "causal_chains": [],
                "key_drivers": ["mock_driver"],
                "_mock": True,
            }, ensure_ascii=False)

        # briefing：返回 BriefingSkill 期望的字段
        if "briefing" in role:
            return json.dumps({
                "title": "[Mock] BTC 决策简报",
                "topic": "BTC",
                "direction": "neutral",
                "setup_quality": "B",
                "executive_summary": "Mock briefing for load-test purposes; no real LLM call performed.",
                "supporting_points": ["Mock support point 1", "Mock support point 2"],
                "contrary_points": ["Mock contrary point 1"],
                "key_risks": ["Mock risk: liquidity"],
                "recommended_action": "watch",
                "invalidation_conditions": ["Price < mock_invalidation_level"],
                "_mock": True,
            }, ensure_ascii=False)

        # strategy：返回 StrategySkill 期望
        if "strategy" in role:
            return json.dumps({
                "strategy_summary": "[Mock] Mean reversion on BTC",
                "match_score": 0.6,
                "rationale": "Mock rationale",
                "_mock": True,
            }, ensure_ascii=False)

        # adjudication：返回 V2 完整裁决结构
        if "adjudication" in role or "verdict" in role or "postprocess" in role:
            return json.dumps({
                "direction": "neutral",
                "conviction": 0.5,
                "recommended_action": "watch",
                "consensus_level": "weak_consensus",
                "synthesis": "[Mock] Evidence is limited; keep the decision in watch mode until stronger confirmation appears.",
                "verdict": "neutral",
                "summary": "[Mock] Adjudication summary",
                "reasoning": "Mock reasoning text.",
                "baseline_score": 0.6,
                "probability_score": 0.5,
                "risk_score": 0.4,
                "structure_score": 0.55,
                "key_agreements": ["Mock agreement"],
                "key_disagreements": [],
                "structure_warnings": [],
                "risk_warnings": [],
                "evidence_quality_warnings": [],
                "invalidation_conditions": ["Mock invalidation condition"],
                "hedging_advice": "[Mock] No specific hedging advice.",
                "action_economics": {"expected_cost_bps": 5, "expected_alpha_bps": 8},
                "_mock": True,
            }, ensure_ascii=False)

        # committee A/B/C/D/E/F / debate：返回 round 解析期望的多字段
        if "committee" in role or "debate" in role:
            return json.dumps({
                "position": "neutral",
                "key_arguments": ["Mock argument 1", "Mock argument 2"],
                "challenges": [],
                "concessions": [],
                "evidence": [],
                "confidence": 0.6,
                "_mock": True,
            }, ensure_ascii=False)

        # G16 evaluator：RAGAS faithfulness / context precision 用 JSON
        if "evaluator" in role or "ragas" in role:
            # 尝试根据 role suffix 区分子任务（faithfulness / relevance / precision）
            if "faithfulness" in role:
                return json.dumps({
                    "claims": ["[Mock] atomic claim 1", "[Mock] atomic claim 2"],
                    "supported": [True, True],
                    "_mock": True,
                }, ensure_ascii=False)
            if "relevance" in role:
                return json.dumps({
                    "questions": [
                        "[Mock] What is the answer about?",
                        "[Mock] How does it relate to the topic?",
                        "[Mock] Why is this relevant?",
                    ],
                    "_mock": True,
                }, ensure_ascii=False)
            if "precision" in role:
                return json.dumps({
                    "verdicts": [True, True, False],
                    "_mock": True,
                }, ensure_ascii=False)
            # 默认 evaluator
            return json.dumps({
                "score": 0.85,
                "_mock": True,
            }, ensure_ascii=False)

        # 兜底：纯文本（chat 模式默认走 fusion，不会到这）
        return f"[Mock LLM response] role={role or 'global'} model={model} — set OPENROUTER_MOCK=0 to use real API."

    def _mock_embedding(self, text: str) -> List[float]:
        """生成确定性 mock embedding（基于文本 hash）。

        - 维度 = settings.EMBEDDING_DIM（默认 2560）
        - 同输入 → 同向量（便于排查向量写入问题）
        - L2 normalize 到单位长度，cosine 距离合理
        """
        import hashlib
        import math

        self._maybe_sleep_mock_latency()
        dim = getattr(settings, "EMBEDDING_DIM", 2560)
        # 用 SHA-256 摘要扩展成确定性的伪随机序列
        seed_bytes = hashlib.sha256((text or "").encode("utf-8")).digest()
        # 把 32 byte 摘要扩展到 dim 个 float（[-1, 1]）
        vec: List[float] = []
        i = 0
        while len(vec) < dim:
            b = seed_bytes[i % 32]
            # 0..255 → -1..1
            vec.append((b - 127.5) / 127.5)
            i += 1
        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def get_last_usage(self) -> dict:
        """获取最近一次调用的 token 使用情况"""
        return self._last_usage.copy()

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """估算调用成本（USD）"""
        prices = self._model_prices(model) or (0.0, 0.0)
        input_cost = (input_tokens / 1_000_000) * prices[0]
        output_cost = (output_tokens / 1_000_000) * prices[1]
        return input_cost + output_cost


# 全局单例
llm_client = LLMClient()
