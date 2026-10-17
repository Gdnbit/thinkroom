"""Final Adjudication Skill

职责：综合委员会分析师（最多6席）的独立分析，给出最终裁决。

V2 升级：
- 支持可选的席位⑤ Truth/Audit 和席位⑥ PnL/Execution
- 裁决输出包含 action_economics（执行经济性评估）
- 强制反证和失效条件
"""
import json
from typing import Optional
from pydantic import BaseModel
from app.config import settings
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse


ALLOWED_RECOMMENDED_ACTIONS = {"enter", "watch", "avoid", "hold", "scale_in", "reduce"}


ADJUDICATION_SYSTEM = """你是一位资深的独立裁决官，负责综合四位分析师的独立分析，做出最终裁决。

核心分析师（必选）：
1. **基准分析师**：基于证据的直觉判断，关注主要矛盾
2. **概率分析师**：用概率框架和期望值做量化分析
3. **风控哨兵**：专注识别尾部风险和决策盲区
4. **市场结构分析师**：分析资金流向、机构行为、庄散博弈、信息不对称

可选分析师（V2 新增，可能不参与每次辩论）：
5. **真伪审计官**：检查证据可靠性、来源可信度、证据冲突、信息缺口
6. **执行经济性顾问**：评估持仓成本、波动率状态、等待价值、执行时机

你的裁决方法：
- 当多数分析师方向一致时，重点采纳概率分析师的量化框架，结合风控和市场结构的防守建议
- 当基准与概率一致但风控/结构不同意时，维持主流方向但必须体现风控和结构分析意见
- 当市场结构分析师发现机构行为与市场叙事背离时，必须在裁决中突出这一警告
- 当分析师方向分歧严重时，以概率分析师的期望值计算为基准，但降低确信度
- 无论何种情况，风控哨兵和市场结构分析师识别的风险必须在裁决中体现
- 如果真伪审计官参与了，必须在 synthesis 中说明证据可靠性对裁决的影响
- 如果执行经济性顾问参与了，裁决必须包含 action_economics 字段

输出 JSON：
- direction: bullish|bearish|neutral  最终方向
- conviction: 0.0-1.0  确信度
- recommended_action: enter|watch|avoid|hold|scale_in|reduce  建议操作
- consensus_level: strong_consensus|moderate_consensus|weak_consensus|split  共识程度
- synthesis: 综合裁决叙述（250字以内，说明采纳了哪些分析、为何如此裁决）
- baseline_score: 0.0-1.0  基准分析师论证质量评分
- probability_score: 0.0-1.0  概率分析师论证质量评分
- risk_score: 0.0-1.0  风控哨兵贡献评分
- structure_score: 0.0-1.0  市场结构分析师贡献评分
- audit_score: 0.0-1.0  真伪审计官贡献评分（如参与）
- key_agreements: 各方共同认可的观点列表
- key_disagreements: 各方分歧最大的观点列表
- risk_warnings: 风控哨兵识别的必须关注的风险列表
- structure_warnings: 市场结构分析师识别的机构行为/庄散博弈警告
- evidence_quality_warnings: 真伪审计官识别的证据质量问题（如参与）
- minority_view: 少数派观点摘要（如有分歧）
- invalidation_conditions: 此裁决失效的条件列表（2-3条，含具体触发阈值）
- hedging_advice: 防守/对冲建议（基于风控哨兵和市场结构分析师意见）
- action_economics: 执行经济性评估（如 PnL 顾问参与，否则为空对象）
  {“funding_cost_annualized”, “volatility_regime”, “waiting_value_window”, “hold_risk_description”, “action_timing”, “action_preference”, “reasoning”}

规则：
- conviction > 0.7 时 consensus_level 可为 strong_consensus
- 方向分歧严重时 consensus_level 为 split，conviction 不超过 0.45
- hedging_advice 不能为空，即使共识强也要有防守建议
- 如果市场结构分析师发现机构与散户行为背离，必须在 synthesis 中明确提及
- 如果真伪审计官发现关键证据未经验证，conviction 应下调 0.1-0.2
- 如果执行经济性顾问认为当前不值得执行，recommended_action 应偏向 watch/avoid
"""


class AdjudicationInput(BaseModel):
    debate_id: str
    question: str
    asset: str
    rounds: list[dict]
    baseline_model: Optional[str] = None
    probability_model: Optional[str] = None
    risk_model: Optional[str] = None
    structure_model: Optional[str] = None
    audit_model: Optional[str] = None
    pnl_model: Optional[str] = None
    activated_seats: list[str] = []


class AdjudicationOutput(BaseModel):
    direction: str = "neutral"
    conviction: float = 0.5
    recommended_action: str = "watch"
    consensus_level: str = "weak_consensus"
    synthesis: str = ""
    baseline_score: float = 0.5
    probability_score: float = 0.5
    risk_score: float = 0.5
    structure_score: float = 0.5
    key_agreements: list[str] = []
    key_disagreements: list[str] = []
    risk_warnings: list[str] = []
    structure_warnings: list[str] = []
    evidence_quality_warnings: list[str] = []
    audit_score: float = 0.0
    action_economics: dict = {}
    minority_view: Optional[str] = None
    invalidation_conditions: list[str] = []
    hedging_advice: Optional[str] = None
    adjudicated_by: Optional[str] = None
    is_mock: bool = False
    error: Optional[str] = None

    # 向后兼容：旧字段映射到新字段
    @property
    def bull_score(self) -> float:
        return self.baseline_score

    @property
    def bear_score(self) -> float:
        return self.risk_score


ROLE_DISPLAY = {
    "baseline": "基准分析师",
    "probability": "概率分析师",
    "risk": "风控哨兵",
    "structure": "市场结构分析师",
    "audit": "真伪审计官",
    "pnl": "执行经济性顾问",
}


class FinalAdjudicationSkill:
    def adjudicate(self, inp: AdjudicationInput) -> AdjudicationOutput:
        """综合三位分析师的独立分析给出最终裁决（B2: 带一次性 JSON 重试）"""
        from app.utils.llm_retry import call_with_schema_retry, RetryableParseError
        user_prompt = self._build_prompt(inp)

        def _llm(sys_p: str, usr_p: str) -> str:
            return llm_client.complete_with_role_str(
                skill_role="committee_postprocess",
                system_prompt=sys_p,
                user_prompt=usr_p,
                max_tokens=max(256, int(settings.COMMITTEE_POSTPROCESS_MAX_TOKENS)),
                temperature=float(settings.COMMITTEE_POSTPROCESS_TEMPERATURE),
            )

        def _parse_and_check(text: str) -> dict:
            data = robust_json_parse(text, context="FinalAdjudication")
            # 核心字段缺失 → 触发重试
            if not data.get("synthesis"):
                raise ValueError("Required field missing: synthesis")
            direction = data.get("direction")
            if direction and direction not in {"bullish", "bearish", "neutral"}:
                raise ValueError(f"Invalid direction: {direction}")
            action = data.get("recommended_action")
            if action and action not in ALLOWED_RECOMMENDED_ACTIONS:
                raise ValueError(f"Invalid recommended_action: {action}")
            return data

        try:
            output = call_with_schema_retry(
                llm_call=_llm,
                system_prompt=ADJUDICATION_SYSTEM,
                user_prompt=user_prompt,
                parser=_parse_and_check,
                validator=self._assemble_from_data,
                context="FinalAdjudication",
            )
            return self._ensure_display_synthesis(output, inp)
        except (RetryableParseError, Exception) as e:
            from loguru import logger
            logger.warning(f"[FinalAdjudication] all attempts failed: {e}")
            return self._fallback_output_from_rounds(inp, str(e)[:200])

    def _ensure_display_synthesis(
        self,
        output: AdjudicationOutput,
        inp: AdjudicationInput,
    ) -> AdjudicationOutput:
        if (output.synthesis or "").strip():
            output.synthesis = output.synthesis.strip()
            return output
        return self._fallback_output_from_rounds(
            inp,
            output.error or "Final adjudication returned empty synthesis",
        )

    def _fallback_output_from_rounds(
        self,
        inp: AdjudicationInput,
        error: str,
    ) -> AdjudicationOutput:
        rounds = list(inp.rounds or [])
        directions = [
            str(r.get("direction", "")).strip()
            for r in rounds
            if str(r.get("direction", "")).strip() in {"bullish", "bearish", "neutral"}
        ]
        direction = self._majority_direction(directions)
        points = self._collect_round_points(rounds)
        roles = []
        for r in rounds:
            role = str(r.get("role", "")).strip()
            label = ROLE_DISPLAY.get(role, role)
            if label and label not in roles:
                roles.append(label)
        participant_text = "、".join(roles[:6]) if roles else "委员会各席位"
        point_text = "；".join(points) if points else "各席位已有发言，但缺少可稳定归纳的论点"
        confidence_values = [
            float(r.get("confidence"))
            for r in rounds
            if isinstance(r.get("confidence"), (int, float))
        ]
        avg_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values else 0.45
        )
        conviction = min(0.55, max(0.25, avg_confidence))
        fallback_warning = (
            "裁决模型未返回可用综合摘要，已用委员会轮次生成保守兜底摘要。"
        )
        return AdjudicationOutput(
            direction=direction,
            conviction=conviction,
            recommended_action="watch",
            consensus_level="weak_consensus",
            synthesis=(
                "裁决兜底：裁决模型未返回可用综合摘要，系统已根据"
                f"{participant_text}的轮次发言生成保守结论。主要可见依据包括："
                f"{point_text}。由于最终裁决文本不完整，本结果仅作为研究线索，"
                "建议保持 watch，并补充证据后复核。"
            ),
            key_agreements=points[:3],
            key_disagreements=[],
            risk_warnings=[
                arg
                for r in rounds
                if r.get("role") == "risk"
                for arg in list(r.get("key_arguments") or [])[:3]
            ],
            structure_warnings=[
                arg
                for r in rounds
                if r.get("role") == "structure"
                for arg in list(r.get("key_arguments") or [])[:3]
            ],
            evidence_quality_warnings=[fallback_warning],
            invalidation_conditions=["补充证据后裁决方向或风险判断发生变化"],
            hedging_advice="在裁决摘要恢复稳定前，不应扩大风险敞口。",
            action_economics={
                "decision_quality": "fallback_from_debate_rounds",
                "reasoning": fallback_warning,
            },
            error=error,
        )

    def _majority_direction(self, directions: list[str]) -> str:
        if not directions:
            return "neutral"
        counts = {direction: directions.count(direction) for direction in set(directions)}
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return "neutral"
        return ranked[0][0]

    def _collect_round_points(self, rounds: list[dict], limit: int = 4) -> list[str]:
        points: list[str] = []
        for r in rounds:
            for raw in list(r.get("key_arguments") or []):
                text = str(raw).strip()
                if text and text not in points:
                    points.append(text[:80])
                if len(points) >= limit:
                    return points
        for r in rounds:
            text = str(r.get("position") or "").strip()
            if text and text not in points:
                points.append(text[:120])
            if len(points) >= limit:
                break
        return points

    def _build_prompt(self, inp: AdjudicationInput) -> str:
        from datetime import datetime
        import zoneinfo
        _tz = zoneinfo.ZoneInfo('Asia/Shanghai')
        _now_str = datetime.now(_tz).strftime('%Y年%m月%d日 %H:%M CST')
        parts = [
            f"当前时间：{_now_str}",
            f"研究问题：{inp.question}",
            f"资产：{inp.asset}",
            "\n【三位分析师的分析内容】",
        ]
        for r in inp.rounds:
            role = r.get("role", "")
            display = ROLE_DISPLAY.get(role, role)
            parts.append(f"\n▶ {display} （第{r.get('round_number',0)}轮，方向：{r.get('direction','未明确')}）")
            parts.append(f"  分析：{r.get('position','')}")
            if r.get("key_arguments"):
                parts.append(f"  论点：" + "；".join(r["key_arguments"]))
            if r.get("challenges"):
                parts.append(f"  质疑：" + "；".join(r["challenges"]))
            if r.get("concessions"):
                parts.append(f"  认可：" + "；".join(r["concessions"]))
            parts.append(f"  信心度：{r.get('confidence', 0.5)}")
        seat_count = len(set(r.get('role', '') for r in inp.rounds))
        parts.append(f"\n请综合以上 {seat_count} 位分析师的独立分析，给出最终裁决 JSON：")
        return "\n".join(parts)

    def _parse(self, text: str) -> AdjudicationOutput:
        """单次解析 + 装配 LLM 原始输出；解析失败时返回带 error 的 AdjudicationOutput。

        与 adjudicate() 内的 _parse_and_check 区别：本方法吞掉异常并在 error 字段
        记录原因（便于上游决定是否降级展示），不触发重试；_parse_and_check 是为
        call_with_schema_retry 设计的，抛异常触发重试。

        此接口供单元测试和任何"已有原始 LLM 文本、只需一次性解析"的场景使用。
        """
        try:
            data = robust_json_parse(text, context="FinalAdjudication")
            if not isinstance(data, dict):
                raise ValueError(f"parsed payload is not a dict: {type(data).__name__}")
            if not data.get("synthesis"):
                # 允许装配出部分字段，但标记 error 让上游知晓不完整
                out = self._assemble_from_data(data)
                out.error = "Required field missing: synthesis"
                return out
            return self._assemble_from_data(data)
        except Exception as e:
            from loguru import logger
            logger.debug(f"[FinalAdjudication] _parse failed: {e}")
            return AdjudicationOutput(error=str(e)[:200])

    def _assemble_from_data(self, data: dict) -> AdjudicationOutput:
        """纯装配：把已解析+校验的 dict 组装为 AdjudicationOutput。

        B2: 失败情形已移到外层 call_with_schema_retry；本方法只做无异常装配。
        """
        is_mock = data.pop("_mock", False) if isinstance(data, dict) else False
        conviction = max(0.0, min(1.0, float(data.get("conviction", 0.5))))
        recommended_action = data.get("recommended_action", "watch")
        if recommended_action not in ALLOWED_RECOMMENDED_ACTIONS:
            recommended_action = "watch"
        return AdjudicationOutput(
            direction=data.get("direction", "neutral"),
            conviction=conviction,
            recommended_action=recommended_action,
            consensus_level=data.get("consensus_level", "weak_consensus"),
            synthesis=data.get("synthesis", ""),
            baseline_score=max(0.0, min(1.0, float(data.get("baseline_score", data.get("bull_score", 0.5))))),
            probability_score=max(0.0, min(1.0, float(data.get("probability_score", 0.5)))),
            risk_score=max(0.0, min(1.0, float(data.get("risk_score", data.get("bear_score", 0.5))))),
            structure_score=max(0.0, min(1.0, float(data.get("structure_score", 0.5)))),
            key_agreements=data.get("key_agreements", []),
            key_disagreements=data.get("key_disagreements", []),
            risk_warnings=data.get("risk_warnings", []),
            structure_warnings=data.get("structure_warnings", []),
            evidence_quality_warnings=data.get("evidence_quality_warnings", []),
            audit_score=max(0.0, min(1.0, float(data.get("audit_score", 0.0)))),
            action_economics=data.get("action_economics", {}),
            minority_view=data.get("minority_view"),
            invalidation_conditions=data.get("invalidation_conditions", []),
            hedging_advice=data.get("hedging_advice"),
            is_mock=is_mock,
        )
