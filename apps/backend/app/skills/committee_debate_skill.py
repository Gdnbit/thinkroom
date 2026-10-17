"""Committee Debate Skill

职责：让四个分析师角色从各自视角独立分析同一问题。
每个角色独立看完同样的证据，自主给出方向判断，而非预设立场。

四席设计：
- Baseline Analyst（基准分析师）：正常视角，基于证据做最直观的分析判断
- Probability Analyst（概率分析师）：看重概率与期望值，量化各情景的加权收益
- Risk Sentinel（风控哨兵）：兜底视角，关注尾部风险、盲区和最坏情况
- Structure Analyst（市场结构分析师）：关注资金流向、机构行为、庄散博弈、信息不对称
"""
import json
from typing import Optional
from pydantic import BaseModel
from app.config import settings
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse


BASELINE_SYSTEM = """你是一位经验丰富的基准分析师（Baseline Analyst）。

你的分析方法：
- 基于给定的所有证据（私域观点、市场数据、事件、资金信号），做出最直观、最符合常理的判断
- 不刻意乐观也不刻意悲观，证据指向哪里你就分析到哪里
- 关注主要矛盾：当前影响该资产的最核心因素是什么？
- 如果其他分析师已发言，针对你认为不合理的分析提出质疑

你不需要预设任何立场。请独立思考后给出你的判断。

输出纯 JSON：
{
  "position": "本轮分析立场陈述（120字以内，说明你的核心判断和依据）",
  "key_arguments": ["核心论点1（注明来源：私域/行情/事件/资金）", "核心论点2"],
  "challenges": ["对其他分析师观点的质疑（如有）"],
  "concessions": ["你认可其他分析师的合理之处（如有）"],
  "confidence": 0.65,
  "direction": "bullish|bearish|neutral"
}"""

PROBABILITY_SYSTEM = """你是一位量化思维的概率分析师（Probability Analyst）。

你的分析方法：
- 识别影响资产走势的 2-3 个关键独立因素
- 为每个因素的不同状态赋予主观概率
- 计算各情景的联合概率和加权期望值
- 最终判断基于期望值而非最高概率情景
- 如果其他分析师已发言，检验他们的隐含概率假设是否合理

你不需要预设任何立场。请用概率框架独立分析后给出判断。

输出纯 JSON：
{
  "position": "本轮分析立场陈述（150字以内，包含关键因素及其概率分布摘要）",
  "key_arguments": ["概率论点1：因素A状态X概率Y%→影响Z", "概率论点2"],
  "challenges": ["对其他分析师概率假设的质疑（如有）"],
  "concessions": ["你认可的其他分析师观点（如有）"],
  "confidence": 0.60,
  "direction": "bullish|bearish|neutral"
}"""

RISK_SYSTEM = """你是一位严谨的风控哨兵（Risk Sentinel）。

你的分析方法：
- 先假设其他分析师的主流判断是正确的，然后专注寻找他们可能忽略的尾部风险
- 关注黑天鹅事件、流动性陷阱、相关性突变、政策突袭等非线性风险
- 评估当前市场情绪是否过热或过冷（极端情绪往往预示反转）
- 明确指出：如果当前共识判断是错误的，最可能因为什么原因？代价有多大？
- 给出防守建议：即使按主流方向操作，应该设置什么止损/对冲条件？

你不需要故意唱反调，但你的核心职责是确保决策的风险敞口被充分识别。

输出纯 JSON：
{
  "position": "风控评估（120字以内，重点说明被忽视的风险和防守建议）",
  "key_arguments": ["风险论点1：具体风险场景及触发条件", "风险论点2"],
  "challenges": ["对其他分析师风险盲区的指出"],
  "concessions": ["你认为当前主流判断合理的部分"],
  "confidence": 0.50,
  "direction": "bullish|bearish|neutral"
}"""

STRUCTURE_SYSTEM = """你是一位市场结构分析师（Market Structure Analyst）。

你的核心使命：透过价格表象，分析谁在买、谁在卖、为什么。

你的分析方法：
- 分析资金流向数据：大户/机构资金进出方向、资金费率极端程度、期货持仓结构变化
- 识别机构行为模式：机构是否在建仓/出货？是否有异常大额订单？持仓集中度如何？
- 解读庄散博弈：散户情绪指标（多空比、社交媒体热度）与价格走势是否背离？
  机构是否可能利用散户情绪进行反向操作？
- 评估消息面意图：近期重大消息是否可能是「有目的的释放」？
  消息的时机和方式是否暗示信息不对称？
- 关注流动性结构：当前流动性是否足以支撑持续走势？是否存在流动性陷阱？
  关键价格区间的挂单分布如何？

核心问题：当前价格走势背后的参与者结构是什么？散户正在被引导去做什么？
聪明钱的实际动向是否与市场叙事一致？

你不需要预设任何立场。请基于市场结构独立分析后给出判断。

输出纯 JSON：
{
  "position": "市场结构评估（150字以内，重点分析参与者行为和信息不对称）",
  "key_arguments": ["结构论点1：机构/大户行为观察及依据", "结构论点2：庄散博弈分析"],
  "challenges": ["对其他分析师忽略的市场结构因素的指出"],
  "concessions": ["你认可的其他分析师观点（如有）"],
  "confidence": 0.55,
  "direction": "bullish|bearish|neutral"
}"""

AUDIT_SYSTEM = """你是一位严谨的真伪审计官（Truth / Evidence Auditor）。

你的核心使命：确保决策所依据的证据是可靠的，而非被未经验证的信息误导。

你的审计方法：
- 逐一检查输入事件的 truth_status：哪些是 confirmed，哪些仍然是 unverified？
- 评估来源可信度（source_tier）：tier1_official > tier2_major_media > tier3_kol > tier4_rumor
- 识别证据冲突：同一事件是否有不同来源给出矛盾的说法？
- 检查观点依据链：私域观点背后引用的证据是否仍然有效？是否已过时或被否认？
- 标注信息缺口：当前决策缺少哪些关键信息？缺失可能导致什么偏差？

你不判断方向，你只评估证据质量。你的输出帮助其他分析师校准 conviction。

输出纯 JSON：
{
  "position": "证据质量评估摘要（120字以内，说明当前证据的整体可靠程度）",
  "key_arguments": ["审计发现1：具体证据的可信度评估", "审计发现2：信息缺口说明"],
  "challenges": ["对其他分析师使用的可疑证据的质疑"],
  "concessions": ["证据链完整可靠的部分"],
  "confidence": 0.50,
  "direction": "neutral"
}"""

PNL_SYSTEM = """你是一位执行经济性顾问（PnL / Execution Advisor）。

你的核心使命：即使方向判断正确，也要评估"现在做这件事值不值"。

你的分析框架：
- 资金费率成本：当前 funding rate 年化后，持仓成本是否过高？
  如果年化 > 20%，长期持仓的成本可能吞噬利润
- 波动率状态：当前波动率处于什么分位？高波动意味着更大止损幅度和更高的被清洗风险
- 等待价值：是否有明确的催化剂时间窗口？等一等是否能获得更好的入场价格？
- 持有风险：如果已有仓位，持有的边际风险是否在增加？
- 执行时机：当前是合适的入场时机，还是应该等待回调/突破确认？

你不需要判断方向。其他分析师已给出方向判断，你评估的是执行层面的经济性。

输出纯 JSON：
{
  "position": "执行经济性评估（120字以内，核心结论是现在做值不值）",
  "key_arguments": ["经济性论点1：资金成本/波动率评估", "经济性论点2：时机与等待价值"],
  "challenges": ["对其他分析师忽略执行成本的指出"],
  "concessions": ["执行条件有利的部分"],
  "confidence": 0.50,
  "direction": "neutral",
  "action_economics": {
    "funding_cost_annualized": null,
    "volatility_regime": "normal",
    "waiting_value_window": "",
    "hold_risk_description": "",
    "action_timing": "wait_for_confirmation",
    "action_preference": "hold_and_wait",
    "reasoning": ""
  }
}"""

# 角色名称映射（新角色名 → 旧 DB 列兼容）
ROLE_DISPLAY_NAMES = {
    "baseline": "基准分析师",
    "probability": "概率分析师",
    "risk": "风控哨兵",
    "structure": "市场结构分析师",
    "audit": "真伪审计官",
    "pnl": "执行经济性顾问",
}

# 角色 → 模型配置 skill_role 映射
ROLE_SKILL_MAP = {
    "baseline": "committee_a",
    "probability": "committee_b",
    "risk": "committee_c",
    "structure": "committee_d",
    "audit": "committee_e",
    "pnl": "committee_f",
}


class DebateRoundInput(BaseModel):
    debate_id: str
    round_number: int
    role: str  # baseline | probability | risk | structure | audit | pnl
    question: str
    asset: str
    context_claims: list[dict] = []
    context_events: list[dict] = []
    context_market: dict = {}
    previous_rounds: list[dict] = []
    # G18 Multi-Agent Retrieval (Anthropic mode)：本角色专属的额外证据
    # 与 shared base context 并列；老调用未传递时 → [] 退化为老路径
    agent_specific_claims: list[dict] = []
    model_override: Optional[str] = None


class DebateRoundOutput(BaseModel):
    role: str
    position: str
    key_arguments: list[str] = []
    challenges: list[str] = []
    concessions: list[str] = []
    confidence: float = 0.5
    direction: str = "neutral"
    is_mock: bool = False
    error: Optional[str] = None


class CommitteeDebateSkill:
    ROLE_SYSTEMS = {
        "baseline": BASELINE_SYSTEM,
        "probability": PROBABILITY_SYSTEM,
        "risk": RISK_SYSTEM,
        "structure": STRUCTURE_SYSTEM,
        "audit": AUDIT_SYSTEM,
        "pnl": PNL_SYSTEM,
    }

    def debate_round(self, inp: DebateRoundInput) -> DebateRoundOutput:
        """执行单轮分析发言"""
        from app.utils.llm_retry import call_with_schema_retry

        system_prompt = self.ROLE_SYSTEMS.get(inp.role, BASELINE_SYSTEM)
        user_prompt = self._build_prompt(inp)
        skill_role = ROLE_SKILL_MAP.get(inp.role, "committee_a")
        role_letter = skill_role.removeprefix("committee_").upper()
        temperature = float(
            getattr(settings, f"COMMITTEE_MODEL_{role_letter}_TEMPERATURE", 0.2) or 0.2
        )
        max_tokens = max(
            256,
            int(getattr(settings, f"COMMITTEE_MODEL_{role_letter}_MAX_TOKENS", 1200) or 1200),
        )
        reasoning_effort = str(
            getattr(settings, f"COMMITTEE_MODEL_{role_letter}_REASONING_EFFORT", "") or ""
        ) or None

        def _llm(sys_p: str, usr_p: str) -> str:
            return llm_client.complete_with_role_str(
                skill_role=skill_role,
                system_prompt=sys_p,
                user_prompt=usr_p,
                max_tokens=max_tokens,
                temperature=temperature,
                model=inp.model_override,
                reasoning_effort=reasoning_effort,
            )

        def _parse_and_check(text: str) -> DebateRoundOutput:
            output = self._parse(inp.role, text)
            if output.error:
                raise ValueError("committee round schema validation failed")
            if not output.position.strip():
                raise ValueError("Required field missing: position")
            return output

        try:
            return call_with_schema_retry(
                llm_call=_llm,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                parser=_parse_and_check,
                validator=lambda output: output,
                context=f"CommitteeDebate:{inp.role}",
            )
        except Exception as error:
            from loguru import logger

            logger.warning(
                f"[CommitteeDebate:{inp.role}] round unavailable after retry: "
                f"{type(error).__name__}"
            )
            return DebateRoundOutput(
                role=inp.role,
                position="Committee seat output unavailable.",
                error="schema_or_provider_failure",
            )

    def _build_prompt(self, inp: DebateRoundInput) -> str:
        from datetime import datetime
        import zoneinfo
        _tz = zoneinfo.ZoneInfo('Asia/Shanghai')
        _now_str = datetime.now(_tz).strftime('%Y年%m月%d日 %H:%M CST')
        role_name = ROLE_DISPLAY_NAMES.get(inp.role, inp.role)
        parts = [
            f"当前时间：{_now_str}",
            f"问题：{inp.question}",
            f"资产：{inp.asset}",
            f"你的角色：{role_name}",
            "\n【证据材料（请基于以下信息独立分析）】",
        ]
        if inp.context_claims:
            parts.append(f"\n私域观点（共享 base，{len(inp.context_claims)} 条）：")
            for c in inp.context_claims[:5]:
                tw = c.get('trust_weight', 0.5)
                conf = c.get('confidence', 0.5)
                parts.append(f"  [{c.get('stance','')}|信任{tw:.1f}|置信{conf:.1f}] {c.get('summary','')}")
        # G18 Multi-Agent Retrieval：本角色额外深挖的证据（与共享证据互补）
        if inp.agent_specific_claims:
            parts.append(
                f"\n【你这一席的专属证据（{role_name} 角度额外拉取，"
                f"{len(inp.agent_specific_claims)} 条）】\n"
                f"这些证据是从你的视角（而非其他角色）补充拉取的，请重点关注："
            )
            for c in inp.agent_specific_claims[:5]:
                tw = c.get('trust_weight', 0.5)
                conf = c.get('confidence', 0.5)
                parts.append(
                    f"  [{c.get('stance','')}|信任{tw:.1f}|置信{conf:.1f}] {c.get('summary','')}"
                )
        if inp.context_market:
            ms = inp.context_market
            parts.append(f"\n市场状态：")
            parts.append(f"  价格 {ms.get('price')}，趋势 {ms.get('trend_direction')}，RSI {ms.get('rsi_14')}，波动 {ms.get('volatility_state')}")
            if ms.get('atr_14'):
                parts.append(f"  ATR(14) {ms.get('atr_14')}，MA20 {ms.get('ma_20')}，MA50 {ms.get('ma_50')}")
        if inp.context_events:
            parts.append(f"\n近期事件（{len(inp.context_events)} 条）：")
            for e in inp.context_events[:3]:
                parts.append(f"  [{e.get('impact_direction','')}|重要性{e.get('importance',0):.1f}] {e.get('title','')}")
        if inp.previous_rounds:
            parts.append("\n【其他分析师已有发言】")
            for r in inp.previous_rounds:
                r_name = ROLE_DISPLAY_NAMES.get(r.get('role', ''), r.get('role', ''))
                parts.append(f"  第{r.get('round')}轮 [{r_name}]: {r.get('position','')}")
        parts.append(f"\n这是第 {inp.round_number} 轮分析。请以{role_name}的视角独立给出你的判断，输出纯 JSON：")
        return "\n".join(parts)

    def _parse(self, role: str, text: str) -> DebateRoundOutput:
        try:
            data = robust_json_parse(text, context="CommitteeDebate")
            is_mock = data.pop("_mock", False)

            # Normalize different LLM response schemas to the expected fields
            # Gemini may wrap everything in a nested dict like {"market_analysis": {...}}
            # Unwrap one level if top-level has no useful fields
            useful_keys = {"position", "thesis", "summary", "key_arguments", "key_drivers",
                           "bear_catalysts", "bull_catalysts", "stance", "direction"}
            if not any(k in data for k in useful_keys):
                for v in data.values():
                    if isinstance(v, dict) and any(k in v for k in useful_keys):
                        data = v
                        break

            position = (data.get("position") or data.get("thesis") or data.get("summary")
                        or data.get("trend_outlook") or data.get("technical_observation") or "")

            key_arguments = data.get("key_arguments") or []
            if not key_arguments:
                # Try alternative field names from various LLM schemas
                for field in ("key_drivers", "bear_catalysts", "bull_catalysts", "catalysts",
                               "supporting_points", "drivers", "reasons", "factors",
                               "main_arguments", "arguments"):
                    val = data.get(field)
                    if isinstance(val, list) and val:
                        key_arguments = [str(v) for v in val[:3]]
                        break
                    elif isinstance(val, dict):
                        flat = []
                        for v in val.values():
                            if isinstance(v, list):
                                flat.extend(str(x) for x in v)
                        if flat:
                            key_arguments = flat[:3]
                            break
            # Handle list-of-dicts (e.g. gemini key_factors: [{"factor": ..., "description": ...}])
            if not key_arguments:
                for field in ("key_factors", "factors", "points"):
                    val = data.get(field)
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        key_arguments = [
                            v.get("description") or v.get("factor") or str(v)
                            for v in val[:3]
                        ]
                        break
            # Last resort: pick first list field with string values
            if not key_arguments:
                for v in data.values():
                    if isinstance(v, list) and v and isinstance(v[0], str):
                        key_arguments = [str(x) for x in v[:3]]
                        break

            challenges = data.get("challenges") or data.get("counterpoints") or data.get("rebuttals") or []
            concessions = data.get("concessions") or data.get("acknowledgements") or []

            # direction — 新角色不预设立场，由模型自主判断
            direction = data.get("direction") or data.get("stance") or "neutral"
            if direction not in ("bullish", "bearish", "neutral"):
                direction = "neutral"

            return DebateRoundOutput(
                role=role,
                position=str(position),
                key_arguments=[str(a) for a in key_arguments[:3]],
                challenges=[str(c) for c in challenges[:2]],
                concessions=[str(c) for c in concessions[:2]],
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                direction=direction,
                is_mock=is_mock,
            )
        except Exception as e:
            return DebateRoundOutput(role=role, position=text[:200], error=f"Parse: {e}")
