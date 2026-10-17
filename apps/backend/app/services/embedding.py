"""Embedding 服务

职责：将文本转为向量（embedding），用于语义搜索。

优先级：
1. EMBEDDING_API_KEY + EMBEDDING_MODEL
2. 回退：OPENROUTER_API_KEY / OPENAI_API_KEY（如配置）
3. 无 Key 时：返回 None（语义搜索退化为关键词搜索）

设计：
- 无 Key 时静默降级，不影响主流程
- 结果通过 Redis 缓存（TTL 24小时），避免重复计费
"""
from typing import Optional, List
from loguru import logger
from app.config import settings
from app.services import cache


def embed_text(text: str) -> Optional[List[float]]:
    """将文本转为 embedding 向量。无 Key 或失败时返回 None。"""
    if not text or not text.strip():
        return None

    # 查缓存（TTL 24小时，embedding 不会变）
    import hashlib
    text_hash = hashlib.md5(text.encode()).hexdigest()
    cache_key = f"adms:emb:{text_hash}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # 优先用专用 embedding key，回退到 OpenRouter / OpenAI（兼容旧配置缺失）
    effective_key = (
        getattr(settings, 'EMBEDDING_API_KEY', '')
        or getattr(settings, 'OPENROUTER_API_KEY', '')
        or getattr(settings, 'OPENAI_API_KEY', '')
    )
    effective_model = getattr(settings, 'EMBEDDING_MODEL', '') or "text-embedding-v4"

    if effective_key and effective_key not in ("", "sk-..."):
        try:
            from openai import OpenAI
            import httpx
            # 使用项目级代理（仅影响本客户端，不改变系统全局网络）
            _proxy = getattr(settings, 'HTTP_PROXY', '') or getattr(settings, 'HTTPS_PROXY', '') or None
            kwargs = {
                "api_key": effective_key,
                "http_client": httpx.Client(proxy=_proxy, timeout=30.0),
            }
            if getattr(settings, 'OPENAI_BASE_URL', ''):
                kwargs["base_url"] = settings.OPENAI_BASE_URL
            elif getattr(settings, 'OPENROUTER_BASE_URL', '') and effective_key == getattr(settings, 'OPENROUTER_API_KEY', ''):
                kwargs["base_url"] = settings.OPENROUTER_BASE_URL
            client = OpenAI(**kwargs)
            resp = client.embeddings.create(
                model=effective_model,
                input=text[:8000],
            )
            vec = resp.data[0].embedding
            cache.set(cache_key, vec, ttl=86400)
            logger.debug(f"[Embed] embedding OK model={effective_model} dim={len(vec)}")
            return vec
        except Exception as e:
            logger.warning(f"[Embed] embedding failed (model={effective_model}): {e}")
            return None

    logger.debug("[Embed] No embedding API key available, skipping")
    return None


def embed_batch(texts: List[str]) -> List[Optional[List[float]]]:
    """批量 embedding，逐条调用（可优化为 batch API）"""
    return [embed_text(t) for t in texts]
