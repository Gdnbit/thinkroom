"""Strategy Matching Skill

职责：将当前市场信号与 StrategyCard 的入场条件对比，生成 SetupCard 评估。
"""
import json
from typing import List, Optional
from pydantic import BaseModel
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse


MATCHING_SYSTEM_PROMPT = """你是一位严格的量化策略评估师。
逐条评估当前市场条件是否满足给定策略的入场条件。

输出 JSON 格式：
- matched_conditions: 已满足的条件列表
- unmatched_conditions: 未满足的条件列表
- disqualifying_flags: 触发的否决条件列表
- strategy_fit_score: 0.0-1.0（matched/total entry conditions）
- evidence_strength_score: 0.0-1.0（基于 Claim 数量和质量）
- overall_score: 0.0-1.0 综合评分
- direction: long|short|neutral
- recommended_action: enter|watch|avoid
- action_notes: 操作说明（50字以内）
- invalidation_conditions: 此 setup 失效的条件列表（最多3条）

规则：触发任何否决条件时 recommended_action 必须为 avoid。
"""


class StrategyMatchingInput(BaseModel):
    strategy_id: str
    strategy_name: str
    strategy_type: str
    entry_conditions: List[str]
    disqualifying_conditions: List[str] = []
    asset: str
    claims: List[dict] = []
    events: List[dict] = []
    market_state: Optional[dict] = None
    flow_signals: List[dict] = []


class SetupEvaluation(BaseModel):
    strategy_id: str
    asset: str
    direction: str = "neutral"
    strategy_fit_score: float = 0.0
    evidence_strength_score: float = 0.0
    overall_score: float = 0.0
    matched_conditions: List[str] = []
    unmatched_conditions: List[str] = []
    disqualifying_flags: List[str] = []
    recommended_action: str = "watch"
    action_notes: Optional[str] = None
    invalidation_conditions: List[str] = []
    is_mock: bool = False
    error: Optional[str] = None


class StrategyMatchingSkill:
    def evaluate(self, input: StrategyMatchingInput) -> SetupEvaluation:
        """评估策略匹配度"""
        if not input.claims and not input.market_state and not input.events:
            return SetupEvaluation(
                strategy_id=input.strategy_id,
                asset=input.asset,
                recommended_action="watch",
                action_notes="信号不足，无法评估",
                error="Insufficient signals",
            )

        user_prompt = self._build_prompt(input)
        try:
            response_text = llm_client.complete_with_role_str(
                skill_role="strategy",
                system_prompt=MATCHING_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1500,
                temperature=0.1,
            )
            return self._parse_response(input.strategy_id, input.asset, response_text)
        except Exception as e:
            return SetupEvaluation(
                strategy_id=input.strategy_id,
                asset=input.asset,
                recommended_action="watch",
                error=str(e),
            )

    def _build_prompt(self, input: StrategyMatchingInput) -> str:
        parts = [
            f"策略：{input.strategy_name}（{input.strategy_type}）",
            f"资产：{input.asset}",
            f"\n【入场条件】（共 {len(input.entry_conditions)} 条）",
        ]
        for i, cond in enumerate(input.entry_conditions, 1):
            parts.append(f"  {i}. {cond}")
        if input.disqualifying_conditions:
            parts.append("\n【否决条件】")
            for c in input.disqualifying_conditions:
                parts.append(f"  - {c}")
        parts.append("\n【当前信号】")
        if input.claims:
            parts.append(f"私域观点（{len(input.claims)} 条）：")
            for c in input.claims[:5]:
                parts.append(f"  [{c.get('stance','')}] {c.get('summary','')}")
        if input.market_state:
            ms = input.market_state
            parts.append(
                f"市场：价格 {ms.get('price')}，趋势 {ms.get('trend_direction')}，"
                f"RSI {ms.get('rsi_14')}，波动 {ms.get('volatility_state')}"
            )
        if input.events:
            parts.append(f"近期事件（{len(input.events)} 条）：")
            for e in input.events[:3]:
                parts.append(f"  [重要性{e.get('importance',0):.1f}] {e.get('title','')}")
        if input.flow_signals:
            parts.append(f"资金信号（{len(input.flow_signals)} 条）：")
            for f_ in input.flow_signals[:3]:
                parts.append(f"  [{f_.get('direction','')}] {f_.get('description','')}")
        parts.append("\n请逐条评估并输出 JSON：")
        return "\n".join(parts)

    def _parse_response(
        self, strategy_id: str, asset: str, response_text: str
    ) -> SetupEvaluation:
        try:
            data = robust_json_parse(response_text, context="StrategyMatching")
            is_mock = data.pop("_mock", False)

            fit = max(0.0, min(1.0, float(data.get("strategy_fit_score", 0.0))))
            strength = max(0.0, min(1.0, float(data.get("evidence_strength_score", 0.0))))
            overall = max(0.0, min(1.0, float(data.get("overall_score", round(fit * 0.6 + strength * 0.4, 3)))))
            disq = data.get("disqualifying_flags", [])
            action = "avoid" if disq else data.get("recommended_action", "watch")

            return SetupEvaluation(
                strategy_id=strategy_id,
                asset=asset,
                direction=data.get("direction", "neutral"),
                strategy_fit_score=fit,
                evidence_strength_score=strength,
                overall_score=overall,
                matched_conditions=data.get("matched_conditions", []),
                unmatched_conditions=data.get("unmatched_conditions", []),
                disqualifying_flags=disq,
                recommended_action=action,
                action_notes=data.get("action_notes"),
                invalidation_conditions=data.get("invalidation_conditions", []),
                is_mock=is_mock,
            )
        except Exception as e:
            return SetupEvaluation(
                strategy_id=strategy_id,
                asset=asset,
                recommended_action="watch",
                error=f"Parse error: {e}",
            )
