"""G16 · RAGAS 自动评估器（轻量本地实现）.

为系统的"答案质量"提供可量化指标，基于 RAGAS 三大核心维度：

1. **Faithfulness（忠实度）**：答案中的 claim 是否能被 retrieved context 支撑？
   - LLM 把 answer 拆成 atomic claims → 对每条判断 context 支持/不支持 → score = supported/total

2. **Answer Relevance（答案相关性）**：答案是否实际回答了 question？
   - LLM 反向：从 answer 生成 N 个潜在的反推 question → 与原 question embedding cosine sim → mean

3. **Context Precision（上下文精度）**：检索到的 context 是不是真的相关？
   - LLM 对每条 context 判 relevant=True/False → 计算 average precision @ k

设计取舍
========
- 不引入 ragas 库（额外依赖、版本耦合 LangChain），手写 prompt + JSON 解析；
- judge LLM 用 settings.OPENROUTER_JUDGE_MODEL（默认走主 chat 模型）；
- OPENROUTER_MOCK=1 时所有指标走 mock 路径返回确定性评分（不烧钱）；
- 解析失败 / LLM 异常 → 记录 warning + 返回 None（让上层显示 "N/A" 而不是假数据）；
- 不持久化结果（先做端点；后续若有需要再加 `evaluation_runs` 表）。

参考：Es et al., "RAGAS: Automated Evaluation of Retrieval Augmented Generation", 2023
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger

from app.config import settings
from app.utils.llm_client import llm_client


# ── 数据类 ──────────────────────────────────────────────────────────


@dataclass
class RAGASResult:
    """三指标评估结果。

    每项 score ∈ [0.0, 1.0] 或 None（解析/LLM 失败）；
    解析失败时同时在 errors 里记录原因。
    """
    faithfulness: Optional[float] = None
    answer_relevance: Optional[float] = None
    context_precision: Optional[float] = None
    judge_model: str = ""
    latency_ms: int = 0
    # 调试信息（前端可选展示）
    n_claims: int = 0           # 答案被拆成多少条 atomic claims
    n_supported: int = 0        # 其中多少条被 context 支撑
    n_relevant_contexts: int = 0
    n_total_contexts: int = 0
    errors: List[str] = field(default_factory=list)
    is_mock: bool = False


# ── Prompt 模板 ──────────────────────────────────────────────────


_FAITHFULNESS_SYSTEM = """You are an objective evaluator. Given a question, an answer, and the
context used to generate the answer, your job is to:
1. Decompose the answer into atomic claims (each one a single self-contained fact).
2. For each claim, judge whether it can be inferred from the context.

Output JSON only:
{
  "claims": ["claim 1", "claim 2", ...],
  "supported": [true, false, ...]   // same length as claims
}
Do not include any other text."""


_RELEVANCE_SYSTEM = """You are an objective evaluator. Given an answer, generate 3 plausible
questions that this answer would be a good response to.

Output JSON only:
{
  "questions": ["q1", "q2", "q3"]
}
Do not include any other text."""


_PRECISION_SYSTEM = """You are an objective evaluator. Given a question and several context passages,
judge whether each passage is relevant to answering the question.

Output JSON only:
{
  "verdicts": [true, false, ...]   // same length as contexts
}
Do not include any other text."""


# ── 工具函数 ────────────────────────────────────────────────────────


def _strip_json(text: str) -> str:
    """LLM 偶尔会用 ```json 包裹；剥掉。"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉首尾 fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_json(text: str) -> Optional[dict]:
    try:
        return json.loads(_strip_json(text))
    except json.JSONDecodeError:
        return None


def _cosine(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1)) or 1.0
    n2 = math.sqrt(sum(b * b for b in v2)) or 1.0
    return max(0.0, min(1.0, dot / (n1 * n2)))


def _avg_precision_at_k(verdicts: List[bool]) -> float:
    """RAGAS Context Precision 公式：sum(P@k * v_k) / total_relevant.

    其中 P@k = relevant_in_top_k / k；v_k 是第 k 位置的相关性 0/1。
    若没有相关 context，返回 0。
    """
    if not verdicts:
        return 0.0
    total_relevant = sum(1 for v in verdicts if v)
    if total_relevant == 0:
        return 0.0
    cumulative = 0
    weighted = 0.0
    for k, v in enumerate(verdicts, start=1):
        if v:
            cumulative += 1
            weighted += cumulative / k
    return weighted / total_relevant


def _judge_model() -> str:
    """返回 judge LLM 模型名。

    优先 `OPENROUTER_JUDGE_MODEL`（用户配置），否则用 chat fusion 主模型。
    """
    return (
        getattr(settings, "OPENROUTER_JUDGE_MODEL", "")
        or getattr(settings, "FUSION_MODEL", "")
        or "deepseek-chat"
    )


# ── 三个独立指标 ────────────────────────────────────────────────────


def evaluate_faithfulness(
    question: str,
    answer: str,
    contexts: List[str],
) -> tuple[Optional[float], int, int, list[str]]:
    """返回 (score, n_claims, n_supported, errors)。"""
    errors: list[str] = []
    if not answer or not contexts:
        return (None, 0, 0, ["empty answer or contexts"])

    user = (
        f"Question: {question}\n\n"
        f"Answer: {answer}\n\n"
        f"Context:\n"
        + "\n---\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    )
    try:
        text, _ = llm_client.complete_with_role(
            skill_role="evaluator_faithfulness",
            system_prompt=_FAITHFULNESS_SYSTEM,
            user_prompt=user,
            max_tokens=800,
            temperature=0.0,
        )
    except Exception as e:
        errors.append(f"LLM call failed: {e}")
        return (None, 0, 0, errors)

    parsed = _safe_json(text)
    if not parsed:
        errors.append("faithfulness: invalid JSON")
        return (None, 0, 0, errors)

    claims = parsed.get("claims") or []
    supported = parsed.get("supported") or []
    if not isinstance(claims, list) or not isinstance(supported, list):
        errors.append("faithfulness: missing claims/supported")
        return (None, 0, 0, errors)
    n = min(len(claims), len(supported))
    if n == 0:
        return (None, 0, 0, ["faithfulness: no claims extracted"])
    n_supported = sum(1 for v in supported[:n] if bool(v))
    return (n_supported / n, n, n_supported, errors)


def evaluate_answer_relevance(
    question: str,
    answer: str,
) -> tuple[Optional[float], list[str]]:
    """LLM 从 answer 反推 N 个 question → 与原 question 求 cosine 相似度均值。"""
    errors: list[str] = []
    if not answer or not question:
        return (None, ["empty answer or question"])

    try:
        text, _ = llm_client.complete_with_role(
            skill_role="evaluator_relevance",
            system_prompt=_RELEVANCE_SYSTEM,
            user_prompt=f"Answer: {answer}",
            max_tokens=400,
            temperature=0.0,
        )
    except Exception as e:
        errors.append(f"LLM call failed: {e}")
        return (None, errors)

    parsed = _safe_json(text)
    if not parsed:
        errors.append("relevance: invalid JSON")
        return (None, errors)

    questions = parsed.get("questions") or []
    if not isinstance(questions, list) or not questions:
        errors.append("relevance: no reverse questions generated")
        return (None, errors)

    # embed 原 question + reverse questions，求 cosine
    try:
        all_texts = [question] + [str(q) for q in questions[:5]]
        embeddings = llm_client.embed(all_texts)
        if not embeddings or len(embeddings) < 2:
            errors.append("relevance: embedding failed")
            return (None, errors)
        q_vec = embeddings[0]
        sims = [_cosine(q_vec, e) for e in embeddings[1:]]
        if not sims:
            return (None, ["relevance: no similarity computed"])
        return (sum(sims) / len(sims), errors)
    except Exception as e:
        errors.append(f"relevance embed failed: {e}")
        return (None, errors)


def evaluate_context_precision(
    question: str,
    contexts: List[str],
) -> tuple[Optional[float], int, int, list[str]]:
    """LLM 标注每条 context 相关与否 → 求 average precision @ k."""
    errors: list[str] = []
    if not contexts:
        return (None, 0, 0, ["empty contexts"])

    user = (
        f"Question: {question}\n\n"
        + "\n---\n".join(f"Context [{i+1}]: {c}" for i, c in enumerate(contexts))
    )
    try:
        text, _ = llm_client.complete_with_role(
            skill_role="evaluator_precision",
            system_prompt=_PRECISION_SYSTEM,
            user_prompt=user,
            max_tokens=400,
            temperature=0.0,
        )
    except Exception as e:
        errors.append(f"LLM call failed: {e}")
        return (None, 0, 0, errors)

    parsed = _safe_json(text)
    if not parsed:
        errors.append("precision: invalid JSON")
        return (None, 0, 0, errors)

    verdicts_raw = parsed.get("verdicts") or []
    if not isinstance(verdicts_raw, list):
        errors.append("precision: verdicts not a list")
        return (None, 0, 0, errors)
    verdicts = [bool(v) for v in verdicts_raw[: len(contexts)]]
    if not verdicts:
        return (None, 0, 0, ["precision: no verdicts"])
    score = _avg_precision_at_k(verdicts)
    return (score, sum(1 for v in verdicts if v), len(verdicts), errors)


# ── 主入口：一次性跑三项 ────────────────────────────────────────────


def evaluate_all(
    question: str,
    answer: str,
    contexts: List[str],
) -> RAGASResult:
    """一次性跑三项 RAGAS 指标，返回 RAGASResult."""
    import time
    start = time.time()
    result = RAGASResult(judge_model=_judge_model())
    result.is_mock = bool(getattr(settings, "OPENROUTER_MOCK", False))

    # Faithfulness
    f_score, n_claims, n_supp, f_errs = evaluate_faithfulness(question, answer, contexts)
    result.faithfulness = f_score
    result.n_claims = n_claims
    result.n_supported = n_supp
    result.errors.extend([f"faithfulness: {e}" for e in f_errs])

    # Answer Relevance
    r_score, r_errs = evaluate_answer_relevance(question, answer)
    result.answer_relevance = r_score
    result.errors.extend([f"relevance: {e}" for e in r_errs])

    # Context Precision
    p_score, n_rel, n_total, p_errs = evaluate_context_precision(question, contexts)
    result.context_precision = p_score
    result.n_relevant_contexts = n_rel
    result.n_total_contexts = n_total
    result.errors.extend([f"precision: {e}" for e in p_errs])

    result.latency_ms = int((time.time() - start) * 1000)
    if any(s is None for s in (f_score, r_score, p_score)):
        logger.warning(
            f"[RAGAS] partial failure — F={f_score} R={r_score} P={p_score} "
            f"({len(result.errors)} errors)"
        )
    else:
        logger.info(
            f"[RAGAS] F={f_score:.2f} R={r_score:.2f} P={p_score:.2f} "
            f"({result.latency_ms}ms, judge={result.judge_model})"
        )
    return result


__all__ = [
    "RAGASResult",
    "evaluate_faithfulness",
    "evaluate_answer_relevance",
    "evaluate_context_precision",
    "evaluate_all",
]
