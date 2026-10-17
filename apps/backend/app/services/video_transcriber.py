"""Video Transcriber — 视频转文字服务

优先级链路：
1. Google AI Studio 直连（完全免费，每日 1500 次）
2. OpenRouter Gemini Flash（极便宜，~$0.10/百万token）

两者都使用 Gemini 2.5 Flash 的多模态视频理解能力：
- 同时处理视频画面 + 音频 + 字幕
- 结合画面上下文（图表、数据）提升转写质量
- 支持中文内容

用途：抖音视频 → 文字稿 → 接入 ingestion pipeline 做观点提取
"""
from typing import Optional
from loguru import logger
from app.config import settings

# 视频转写的统一 prompt
_TRANSCRIBE_PROMPT = (
    "请将这个视频的完整内容转化为结构化的文字稿。\n"
    "要求：\n"
    "1. 完整记录视频中的口述内容（逐句转写）\n"
    "2. 如果视频有内嵌字幕，优先以字幕为准\n"
    "3. 描述关键的画面信息（图表、数据、屏幕截图等）\n"
    "4. 如果涉及金融/投资/市场观点，请特别标注看多/看空立场和具体资产\n"
    "5. 输出纯文本，不需要时间戳\n"
)


def _try_google_ai_studio(video_url: str, prompt: str, max_tokens: int) -> Optional[str]:
    """方案一：Google AI Studio 直连（免费）

    需要 GOOGLE_AI_API_KEY（在 https://aistudio.google.com 免费申请）。
    免费层：每分钟 15 次、每天 1500 次，对跟踪几个博主绰绰有余。
    """
    api_key = settings.GOOGLE_AI_API_KEY
    if not api_key:
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(settings.VIDEO_TRANSCRIBE_MODEL)

        # Gemini API 原生视频理解：传 URL，模型自动处理画面+音频+字幕
        response = model.generate_content(
            [
                {"file_data": {"file_uri": video_url, "mime_type": "video/mp4"}},
                prompt,
            ],
            generation_config=genai.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.1,
            ),
        )
        text = response.text or ""
        if text:
            logger.info(f"[VideoTranscriber] Google AI Studio: {len(text)} chars")
            return text
    except ImportError:
        logger.debug("[VideoTranscriber] google-generativeai not installed, trying OpenAI-compatible API")
        # 回退：用 OpenAI 兼容格式直连 Google AI Studio
        return _try_google_ai_openai_compat(video_url, prompt, max_tokens, api_key)
    except Exception as e:
        logger.debug(f"[VideoTranscriber] Google AI Studio native failed: {e}")
        # 回退：用 OpenAI 兼容格式
        return _try_google_ai_openai_compat(video_url, prompt, max_tokens, api_key)

    return None


def _try_google_ai_openai_compat(video_url: str, prompt: str, max_tokens: int, api_key: str) -> Optional[str]:
    """Google AI Studio 的 OpenAI 兼容接口（无需安装 google-generativeai 包）"""
    try:
        import openai
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        resp = client.chat.completions.create(
            model=settings.VIDEO_TRANSCRIBE_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=max_tokens,
            temperature=0.1,
        )
        text = resp.choices[0].message.content or ""
        if text:
            logger.info(f"[VideoTranscriber] Google AI (OpenAI-compat): {len(text)} chars")
            return text
    except Exception as e:
        logger.debug(f"[VideoTranscriber] Google AI OpenAI-compat failed: {e}")
    return None


def _try_openrouter(video_url: str, prompt: str, max_tokens: int) -> Optional[str]:
    """方案二：通过 OpenRouter 调用 Gemini（付费但极便宜）"""
    try:
        from app.utils.llm_client import llm_client
        client = llm_client._get_openai_client()
        if not client:
            return None

        model = settings.VIDEO_TRANSCRIBE_FALLBACK_MODEL
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=max_tokens,
            temperature=0.1,
            extra_headers={
                "HTTP-Referer": settings.OPENROUTER_HTTP_REFERER,
                "X-Title": settings.OPENROUTER_APP_TITLE,
            },
        )
        text = resp.choices[0].message.content or ""
        if text:
            logger.info(f"[VideoTranscriber] OpenRouter ({model}): {len(text)} chars")
            # 记录成本
            try:
                from app.services.cost_tracker import track_usage
                usage = resp.usage
                if usage:
                    track_usage(
                        model=model,
                        input_tokens=usage.prompt_tokens,
                        output_tokens=usage.completion_tokens,
                        role="video_transcribe",
                    )
            except Exception:
                pass
            return text
    except Exception as e:
        logger.debug(f"[VideoTranscriber] OpenRouter failed: {e}")
    return None


def transcribe_video(video_url: str, hint: str = "", max_tokens: int = 0) -> str:
    """将视频 URL 转化为文字稿

    链路：Google AI Studio(免费) → OpenRouter(付费备选)

    Args:
        video_url: 视频的公开 URL
        hint: 视频标题/描述，帮助模型理解上下文
        max_tokens: 最大输出 token 数，0 表示使用配置默认值

    Returns:
        视频内容的文字稿
    """
    if not video_url:
        return ""

    tok = max_tokens or settings.VIDEO_TRANSCRIBE_MAX_TOKENS
    prompt = _TRANSCRIBE_PROMPT
    if hint:
        prompt += f"\n视频标题/描述：{hint}\n"

    # 1. 尝试 Google AI Studio 直连（免费）
    result = _try_google_ai_studio(video_url, prompt, tok)
    if result:
        return result

    # 2. 回退到 OpenRouter（付费但便宜）
    result = _try_openrouter(video_url, prompt, tok)
    if result:
        return result

    return f"[视频转写失败] {hint}"


def transcribe_video_safe(video_url: str, hint: str = "") -> Optional[str]:
    """安全版本：失败返回 None 而非抛异常"""
    try:
        result = transcribe_video(video_url, hint)
        if result.startswith("[视频转写失败"):
            return None
        return result
    except Exception as e:
        logger.debug(f"[VideoTranscriber] Safe transcribe failed: {e}")
        return None
