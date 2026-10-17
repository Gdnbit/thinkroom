"""Briefing Skill

职责：基于融合结果生成结构化的研究简报（BriefingCard）。

规则：
- 调用 LLM 生成最终简报
- Skill 只返回结构化结果，不写数据库
"""
import json
import re
from typing import List, Optional
from pydantic import BaseModel
from app.config import settings
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse
from app.skills.fusion_skill import FusionOutput


BRIEFING_SYSTEM_PROMPT = """你是一位专业的投资研究分析师，擅长第一性原理推演和战情式情景分析。
你的任务是基于融合分析结果，生成一份有深度的研究简报。

重要：你将在 user prompt 中收到「当前日期」，请严格以该日期为时间基准。
所有价格引用、时间预期（如「降息时间表」「关键事件」）必须基于该日期之后的合理预判，不得引用该日期之前已经发生或过期的事件作为"未来预期"。

## 上下文数据来源
- 私域观点：用户收集的高质量研究资料（含来源可信度权重）
- 实时行情：Binance/Yahoo Finance 的价格和技术指标
- 资金信号：Binance Futures 的资金费率/OI/多空比
- 市场事件：财联社/Alpha Vantage 等的最新新闻

## 输出格式（严格 JSON，所有字段值必须是字符串或字符串数组，不得使用嵌套对象）
{
  "title": "资产名 + 核心判断（20字以内）",
  "topic": "研究主题（10字以内）",
  "executive_summary": "当前状态描述（120字以内）：说明现在处于什么格局、最关键的驱动因素是什么、确信度如何",
  "supporting_points": [
    "论点一，末尾注明（私域观点）",
    "论点二（资金信号）"
  ],
  "contrary_points": [
    "反驳一（市场事件）"
  ],
  "key_risks": [
    "风险一，含具体触发条件或量化阈值（市场事件）"
  ],
  "strategy_fit_summary": "见下方四层结构要求",
  "recommended_action": "watch",
  "invalidation_conditions": [
    "触发条件一，含具体价格水平或事件（行情数据）"
  ]
}

## strategy_fit_summary 情景推演要求（战情分析风格，因素排列组合结构）

strategy_fit_summary 是本简报最核心的部分，必须体现完整的逻辑链条，按以下结构输出：

【基本面定锚】
用第一性原理回答：这个资产当前处于什么结构性位置？核心驱动力是什么？要说清楚"为什么"，不要废话。

【关键影响因素识别】
识别出当前真正影响走势方向的独立因素（2-3个），每个因素列出其可能的状态（每个因素2-4种状态），并给出每种状态的主观概率估计（各状态概率之和=100%）。
格式：
因素一：[因素名称]——状态①[描述]（概率X%），状态②[描述]（概率X%），状态③[描述]（概率X%，如有）
因素二：[因素名称]——状态a[描述]（概率X%），状态b[描述]（概率X%）
注意：因素要真正独立，不能互相包含；状态要互斥且完备，概率之和=100%。

【情景排列组合推演】
将上述因素的状态进行排列组合，用矩阵表格呈现，规则如下：

▸ 当有 2 个独立因素时，输出一张矩阵表：
  - 行 = 因素一的各状态（①②③…），列 = 因素二的各状态（a b c…）
  - 每个单元格填写：联合概率% | 方向 | 核心结论（一句话）
  - 格式（使用 Markdown 表格）：
    | 因素一＼因素二 | 状态a（X%） | 状态b（X%） |
    |---|---|---|
    | 状态①（X%） | ①a: 联合X% \\| 方向 \\| 结论 | ①b: 联合X% \\| 方向 \\| 结论 |
    | 状态②（X%） | ②a: 联合X% \\| 方向 \\| 结论 | ②b: 联合X% \\| 方向 \\| 结论 |
  - 表格之后补充：各联合概率之和=100%的验证，以及最高概率情景的触发条件说明

▸ 当有 3 个独立因素时，固定因素三的每个状态，分别输出一张 2D 矩阵表：
  - 表一（因素三=状态Ⅰ）：行=因素一，列=因素二，单元格=三因素联合概率%
  - 表二（因素三=状态Ⅱ）：同上
  - 每张表下方注明该因素三状态的边际概率，以及此子矩阵内概率之和

▸ 概率规则：联合概率 = 各因素状态概率之积（若独立），所有情景概率之和=100%
▸ 注意：Markdown 表格中的竖线 | 在单元格内容里需用 \\| 转义

【期望值加权决策】
这是最终的决策层，不能简单选最高概率情景，而要做加权期望值推导：
步骤一：列出每个情景下的最优操作（enter/watch/avoid/hold）及其预期收益/风险比（如"做多，预期+20%，止损-8%"）
步骤二：用概率加权计算各操作的期望值，格式如：
  enter 期望值 = [情景①a概率×收益] + [情景②a概率×收益] - [亏损情景概率×损失]
  watch 期望值 = 0（不操作）
步骤三：给出最终建议，格式：「当前最优选择是[操作]，加权期望优于[其他选项]，核心逻辑是[一句话说明为何此操作在概率加权后占优]。触发调整的条件：若[X]则切换为[Y操作]。」
注意：如果最高概率情景与期望值最优操作不一致，必须说明原因（通常是某个低概率情景的尾部风险过大）。

## 关键要求
- supporting_points / contrary_points / key_risks / invalidation_conditions 必须是纯字符串数组，每条末尾注明来源
- recommended_action 只能是 enter | watch | avoid | hold 之一
- conviction < 0.5 时，recommended_action 必须是 watch 或 avoid
- strategy_fit_summary 是单个字符串，其中换行用 \\n 表示，段落之间用 \\n\\n 分隔；情景矩阵表使用标准 Markdown 表格语法（| 列1 | 列2 |），表格内单元格里的竖线用 \\| 转义
- 直接返回 JSON，不要有任何其他文字、markdown 格式或代码块标记
"""


GENERAL_EVIDENCE_BRIEFING_SYSTEM_PROMPT = """你是一位严谨的证据研究分析师。请仅依据输入的融合结果，生成一份简洁、可审计的资料简报。

## 输出边界
- 不得补充输入中不存在的事实、时间、机构、人物、价格或市场状态。
- 明确区分“资料明确说明”“基于资料的推论”“尚待验证”。
- 不得把融合层确信度或内部评分写成证据事实；可信程度只做定性说明，除非原始证据明确给出该数值。
- 资料不足时必须写明边界，不得生成方向性交易判断。
- GENERAL 资料简报的 recommended_action 固定为 watch。

## 输出格式
严格返回 JSON，字段值只能是字符串或字符串数组，不得使用嵌套对象：
{
  "title": "资料主题 + 核心结论（20字以内）",
  "topic": "研究主题（10字以内）",
  "executive_summary": "参考时间、已确认内容、证据边界和可信程度（180字以内）",
  "supporting_points": ["可由输入证据直接支持的事实或判断（私域观点）"],
  "contrary_points": ["相反证据、冲突信息或当前缺失的交叉验证（证据边界）"],
  "key_risks": ["误用、过度外推、时效性或范围风险（证据边界）"],
  "strategy_fit_summary": "证据逻辑链：前提 -> 原始证据 -> 可支持判断 -> 不可支持判断 -> 待验证条件",
  "recommended_action": "watch",
  "invalidation_conditions": ["哪些新证据会使当前判断失效（证据边界）"]
}

直接返回 JSON，不要输出代码块、内部规则或额外解释。
"""


class BriefingInput(BaseModel):
    question: str
    asset: str
    fusion_result: FusionOutput
    time_horizon: Optional[str] = None
    history: List[dict] = []  # [{"role": "user"|"assistant", "content": str}]
    # 原始市场数据，用于情景推演
    market_state: Optional[dict] = None
    flow_signals: List[dict] = []
    events: List[dict] = []


class BriefingOutput(BaseModel):
    title: str
    topic: str
    asset: str
    direction: str
    setup_quality: str
    time_horizon: Optional[str]
    executive_summary: str
    supporting_points: List[str] = []
    contrary_points: List[str] = []
    key_risks: List[str] = []
    strategy_fit_summary: Optional[str] = None
    recommended_action: str
    invalidation_conditions: List[str] = []
    model_used: str = "claude-3-opus-20240229"
    is_mock: bool = False
    error: Optional[str] = None


class BriefingSkill:
    def execute(self, input: BriefingInput) -> BriefingOutput:
        """生成研究简报"""
        fusion = input.fusion_result

        # 如果信号不足，直接返回
        if fusion.setup_quality == "insufficient":
            return BriefingOutput(
                title=f"{input.asset} 信号不足",
                topic=input.asset,
                asset=input.asset,
                direction="neutral",
                setup_quality="insufficient",
                time_horizon=input.time_horizon,
                executive_summary="当前信号数量不足以支撑有效研究判断，建议继续收集资料。",
                supporting_points=[],
                contrary_points=[],
                key_risks=["信号不足"],
                recommended_action="watch",
                invalidation_conditions=[],
                error=fusion.error,
            )

        user_prompt = self._build_prompt(input)
        compact_general_profile = self._use_general_evidence_profile(input)
        system_prompt = (
            GENERAL_EVIDENCE_BRIEFING_SYSTEM_PROMPT
            if compact_general_profile
            else BRIEFING_SYSTEM_PROMPT
        )
        max_tokens = max(
            256,
            int(
                settings.BRIEFING_GENERAL_MAX_TOKENS
                if compact_general_profile
                else settings.BRIEFING_MAX_TOKENS
            ),
        )
        temperature = float(settings.BRIEFING_TEMPERATURE)
        # B2: 带一次性重试的 LLM 调用，捕获 JSON/Schema 失败后附加纠错指令重试
        from app.utils.llm_retry import call_with_schema_retry, RetryableParseError
        model_used_ref = {"value": "unknown"}

        def _llm_call(sys_p: str, usr_p: str) -> str:
            text, mdl = llm_client.complete_with_role(
                skill_role="briefing",
                system_prompt=sys_p,
                user_prompt=usr_p,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            model_used_ref["value"] = mdl
            return text

        def _parse_and_check(text: str):
            """解析 + strict 校验：核心字段必须非空，否则重试"""
            data = robust_json_parse(text, context="BriefingSkill")
            # 必填字段缺失 → 触发重试
            if not data.get("executive_summary"):
                raise ValueError("Required field missing: executive_summary")
            action = data.get("recommended_action")
            if action and action not in {"enter", "watch", "avoid", "hold"}:
                raise ValueError(f"Invalid recommended_action: {action}")
            return data

        try:
            result = call_with_schema_retry(
                llm_call=_llm_call,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                parser=_parse_and_check,
                validator=lambda data: self._assemble_output(input, data),
                context="BriefingSkill",
            )
            result.model_used = model_used_ref["value"]
            return result
        except (RetryableParseError, Exception) as e:
            from loguru import logger
            logger.warning(f"[BriefingSkill] all attempts failed, using fallback: {e}")
            return BriefingOutput(
                title=f"{input.asset} 简报生成失败",
                topic=input.asset,
                asset=input.asset,
                direction=fusion.direction,
                setup_quality=fusion.setup_quality,
                time_horizon=input.time_horizon,
                executive_summary=fusion.synthesis_narrative,
                recommended_action=fusion.recommended_action,
                error=str(e)[:200],
                model_used=model_used_ref["value"],
            )

    @staticmethod
    def _use_general_evidence_profile(input: BriefingInput) -> bool:
        return (
            input.asset.strip().upper() == "GENERAL"
            and not input.market_state
            and not input.flow_signals
            and not input.events
        )

    def _build_prompt(self, input: BriefingInput) -> str:
        from datetime import datetime
        import zoneinfo
        tz_cst = zoneinfo.ZoneInfo('Asia/Shanghai')
        today = datetime.now(tz_cst).strftime('%Y年%m月%d日 %H:%M CST')
        fusion = input.fusion_result
        parts = [
            f"当前日期：{today}（请以此为基准，不要引用过期价格或已过去的预期时间窗口）",
            f"研究问题：{input.question}",
            f"资产：{input.asset}",
            f"\n## 综合判断（Fusion 层结果）",
            f"- 方向：{fusion.direction}（确信度 {fusion.conviction:.0%}）",
            f"- 机会质量：{fusion.setup_quality}",
            f"- 建议操作：{fusion.recommended_action}",
            f"- 综合叙述：{fusion.synthesis_narrative}",
        ]
        if fusion.key_supporting_factors:
            parts.append("\n支撑因素：")
            for f in fusion.key_supporting_factors:
                parts.append(f"- {f}")
        if fusion.key_risk_factors:
            parts.append("\n风险因素：")
            for r in fusion.key_risk_factors:
                parts.append(f"- {r}")

        # 注入原始市场数据供 strategy_fit_summary 情景推演使用
        ms = input.market_state or {}
        if ms:
            parts.append("\n## 实时行情数据")
            for k in ("price", "change_24h", "rsi_14", "ma_20", "ma_50", "atr_14", "volume_24h"):
                if k in ms:
                    parts.append(f"- {k}: {ms[k]}")

        # 资金信号
        flow_sigs = input.flow_signals or []
        if flow_sigs:
            parts.append("\n## 资金流向信号")
            for sig in flow_sigs:
                if not isinstance(sig, dict):
                    continue
                desc = sig.get("description")
                if desc:
                    parts.append(f"- {desc}")
                else:
                    st = sig.get("signal_type", "")
                    rv = sig.get("raw_value", "")
                    if st:
                        parts.append(f"- {st}: {rv}")

        # 近期关键事件
        events = input.events or []
        if events:
            parts.append("\n## 近期关键事件（按重要性排序）")
            for ev in sorted(events, key=lambda x: x.get('importance', 0), reverse=True)[:5]:
                title = ev.get('title') or ev.get('summary', '')[:80]
                src = ev.get('source', '')
                imp = ev.get('importance', 0)
                parts.append(f"- [{imp:.1f}] {title}（{src}）")

        parts.append("\n请生成研究简报（JSON 格式）：")
        if getattr(input, 'history', None):
            h_parts = ['\n## 历史对话记录（请结合上下文回答当前问题）']
            for h in input.history[-6:]:
                role_label = '用户' if h.get('role') == 'user' else '助手'
                h_parts.append(f"{role_label}：{str(h.get('content', ''))[:300]}")
            h_parts.append('---')
            parts = h_parts + parts
        return "\n".join(parts)

    def _flatten_points(self, items: list) -> list:
        """将 [{point, source}, ...] 或 [str, ...] 统一转为字符串列表"""
        result = []
        for item in (items or []):
            if isinstance(item, dict):
                point = item.get('point') or item.get('text') or item.get('content') or ''
                source = item.get('source') or item.get('type') or ''
                result.append(f"{point}（{source}）" if source else point)
            elif isinstance(item, str):
                result.append(item)
        return result

    def _assemble_output(self, input: BriefingInput, data: dict) -> BriefingOutput:
        """纯装配：把已解析+校验的 dict 组装为 BriefingOutput。

        B2 重构：此方法不再处理 JSON 解析失败；失败情形由 call_with_schema_retry
        抛出 RetryableParseError，由 execute() 的外层 fallback 处理。
        """
        fusion = input.fusion_result
        is_mock = data.pop("_mock", False) if isinstance(data, dict) else False
        return BriefingOutput(
            title=data.get("title", f"{input.asset} 研究简报"),
            topic=data.get("topic", input.asset),
            asset=input.asset,
            direction=fusion.direction,
            setup_quality=fusion.setup_quality,
            time_horizon=data.get("time_horizon") or input.time_horizon or fusion.time_horizon,
            executive_summary=data.get("executive_summary", fusion.synthesis_narrative),
            supporting_points=self._flatten_points(data.get("supporting_points")) or fusion.key_supporting_factors or [],
            contrary_points=self._flatten_points(data.get("contrary_points")) or [f"[风险] {r}" for r in fusion.key_risk_factors[:3]] or [],
            key_risks=self._flatten_points(data.get("key_risks")) or fusion.key_risk_factors or [],
            strategy_fit_summary=data.get("strategy_fit_summary"),
            recommended_action=data.get("recommended_action", fusion.recommended_action),
            invalidation_conditions=data.get("invalidation_conditions", []),
            is_mock=is_mock,
        )
