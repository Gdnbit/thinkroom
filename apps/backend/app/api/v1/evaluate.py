"""G16 · /api/v1/evaluate — RAGAS 自动评估端点.

POST /api/v1/evaluate
=====================

判断一个 (question, answer, contexts) 三元组在 RAGAS 三大维度上的得分：
- faithfulness: 答案是否被 context 支撑
- answer_relevance: 答案是否真的回答了 question
- context_precision: context 里多少条真的相关

典型用法
--------
1. **运营调试**：跑一段 chat 后调本端点判断答案质量
2. **批量回归**：用脚本对历史 chat session 跑一遍
3. **阈值告警**：在 dashboard 显示 7 天均值，跌破阈值时报警（后续）

API key 认证由 main.py 的中间件统一处理（P0-1），端点本身无需额外 dep。

注意
----
- 实际调 LLM；不开 OPENROUTER_MOCK 时按 judge_model 计费
- 单次调用 3 次 LLM + 1 次 embed（成本 ~$0.005-$0.02 视模型）
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.ragas_evaluator import evaluate_all


router = APIRouter()


# ── Pydantic schemas ────────────────────────────────────────────────


class EvaluateRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    answer: str = Field(..., min_length=1, max_length=20000)
    contexts: List[str] = Field(..., min_length=1, max_length=50)


class EvaluateResponse(BaseModel):
    faithfulness: Optional[float]
    answer_relevance: Optional[float]
    context_precision: Optional[float]
    judge_model: str
    latency_ms: int
    n_claims: int
    n_supported: int
    n_relevant_contexts: int
    n_total_contexts: int
    is_mock: bool
    errors: List[str]


# ── 端点 ────────────────────────────────────────────────────────────


@router.post("", response_model=EvaluateResponse)
def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    """同步跑 RAGAS 三项评估。

    单次请求 ~3-5 秒（按 judge_model 速度），不要在主链路里调；
    建议作为后台 / 调试工具用。
    """
    try:
        result = evaluate_all(
            question=req.question,
            answer=req.answer,
            contexts=list(req.contexts),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"evaluate_all failed: {e}")

    return EvaluateResponse(
        faithfulness=result.faithfulness,
        answer_relevance=result.answer_relevance,
        context_precision=result.context_precision,
        judge_model=result.judge_model,
        latency_ms=result.latency_ms,
        n_claims=result.n_claims,
        n_supported=result.n_supported,
        n_relevant_contexts=result.n_relevant_contexts,
        n_total_contexts=result.n_total_contexts,
        is_mock=result.is_mock,
        errors=result.errors,
    )
