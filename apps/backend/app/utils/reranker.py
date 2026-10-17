r"""A4/G11 · BGE Cross-Encoder Reranker（二阶段精排）

职责：
    在初步检索（bi-encoder cosine）之后做二阶段精排。Cross-Encoder 同时看 query
    和 candidate，对 "semantic match" 的判断比 bi-encoder 准得多，尤其是对
    "真实相关 vs 近义但无关" 的区分。业界标准做法（Anthropic / LangChain）。

模型：
    默认用 BAAI/bge-reranker-v2-m3（~278MB，多语言中英友好，CPU 可跑）
    可通过 HF_HOME 环境变量指定自己的模型缓存目录：

        $env:HF_HOME = "<model-cache-directory>"
        python -c "from FlagEmbedding import FlagReranker; FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=True)"

    首次 import FlagReranker 会触发下载。或在 settings.RERANKER_MODEL_PATH 指向
    已下载的本地路径。

使用：
    from app.utils.reranker import get_reranker
    reranker = get_reranker()
    if reranker is not None:
        scored = reranker.rerank(query, candidate_texts)  # List[(text, score)]

设置：
    ENABLE_RERANKER: bool（默认 False）
    RERANKER_MODEL: 模型名或本地路径（默认 BAAI/bge-reranker-v2-m3）
    RERANKER_TOP_K: 精排后保留条数（默认 8）
    RERANKER_WEIGHT: 融合到综合评分中的权重（默认 0.25）
"""
from __future__ import annotations

import os
from typing import List, Optional, Protocol, Tuple

from loguru import logger


class Reranker(Protocol):
    """Reranker 接口协议。所有实现必须提供 rerank()。"""
    def rerank(self, query: str, candidates: List[str]) -> List[Tuple[int, float]]:
        """输入 query + N 条候选文本，返回按相关性降序排列的 (原始 index, score)。

        score 取值范围：bge-reranker-v2-m3 是 sigmoid 后的 [0, 1]。
        """
        ...


class _BGEReranker:
    """包装 FlagEmbedding.FlagReranker；lazy-load。"""

    def __init__(self, model_name: str, use_fp16: bool = True):
        self._model_name = model_name
        self._use_fp16 = use_fp16
        self._impl = None

    def _ensure_loaded(self) -> bool:
        if self._impl is not None:
            return True
        try:
            from FlagEmbedding import FlagReranker  # type: ignore
        except ImportError:
            logger.warning(
                "[Reranker] FlagEmbedding not installed. "
                "Run: pip install FlagEmbedding"
            )
            return False
        try:
            logger.info(f"[Reranker] Loading {self._model_name} (use_fp16={self._use_fp16})...")
            self._impl = FlagReranker(self._model_name, use_fp16=self._use_fp16)
            logger.info(f"[Reranker] {self._model_name} loaded")
            return True
        except Exception as e:
            logger.warning(f"[Reranker] Failed to load {self._model_name}: {e}")
            return False

    def rerank(self, query: str, candidates: List[str]) -> List[Tuple[int, float]]:
        if not candidates:
            return []
        # P2-3: 降级路径 —— 保留初级召回顺序，避免抹平为统一 0.5 丢失相关性信息
        # 返回形如 [(idx, 1.0 - idx/n), ...] 的 rank 归一分数，和调用方 fallback
        # 行为一致（claim_post_processor._score 的 else 分支）
        def _fallback_by_rank() -> List[Tuple[int, float]]:
            n = max(len(candidates), 1)
            return [(i, 1.0 - i / n) for i in range(len(candidates))]

        if not self._ensure_loaded():
            return _fallback_by_rank()

        pairs = [[query, c] for c in candidates]
        try:
            scores = self._impl.compute_score(pairs, normalize=True)  # sigmoid → [0, 1]
            # 单条时 FlagReranker 可能返回 scalar，统一成 list
            if not isinstance(scores, list):
                scores = [scores]
            ranked = sorted(
                [(i, float(s)) for i, s in enumerate(scores)],
                key=lambda x: x[1],
                reverse=True,
            )
            return ranked
        except Exception as e:
            logger.warning(f"[Reranker] compute_score failed: {e}")
            return _fallback_by_rank()


# ── 模块级单例（lazy） ───────────────────────────────────────
_singleton: Optional[_BGEReranker] = None
_singleton_tried = False


def get_reranker() -> Optional[Reranker]:
    """获取全局 reranker 单例；未启用或加载失败返回 None（调用方降级处理）。

    调用方惯用法：
        r = get_reranker()
        if r is None:
            return initial_candidates  # 降级：跳过精排
        ranked = r.rerank(query, [c.summary for c in initial_candidates])
    """
    global _singleton, _singleton_tried
    from app.config import settings

    if not getattr(settings, "ENABLE_RERANKER", False):
        return None

    if _singleton is None and not _singleton_tried:
        _singleton_tried = True
        # 尊重调用者的缓存配置；留空时使用模型库默认值。
        if not os.environ.get("HF_HOME") and getattr(settings, "HF_HOME", ""):
            default_hf_home = settings.HF_HOME
            os.environ["HF_HOME"] = default_hf_home
            logger.info(f"[Reranker] HF_HOME not set, defaulting to {default_hf_home}")

        model_name = getattr(settings, "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
        _singleton = _BGEReranker(model_name, use_fp16=True)

    return _singleton


def reset_reranker() -> None:
    """测试用：重置单例，允许重新加载。"""
    global _singleton, _singleton_tried
    _singleton = None
    _singleton_tried = False


__all__ = ["Reranker", "get_reranker", "reset_reranker"]
