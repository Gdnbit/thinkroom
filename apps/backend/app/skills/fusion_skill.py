"""Fusion Skill

职责：融合多源信号（Claim + Event + Market + Flow），生成综合判断。

规则：
- 调用 LLM 进行融合分析
- Skill 只返回结构化结果，不写数据库
"""
import json
from typing import List, Optional
from pydantic import BaseModel
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse


FUSION_SYSTEM_PROMPT = """你是一位资深的量化研究员和投资决策顾问。
你的任务是将来自多个维度的信号融合成一个结构化的综合判断。

## 数据来源说明（重要：请区别对待不同来源的可信度）

1. **私域观点（Claim）**：用户手动收集的私域资料，包含研究报告、专家发言、截图记录等。
   - 每条含 trust_weight（来源可信度 0-1）和 confidence（观点置信度 0-1）
   - is_contradicted=true 表示该观点已被其他观点矛盾，权重应降低
   - 这是最有差异化价值的信息，优先参考高 trust_weight 的观点

2. **市场事件（Event）**：来自财联社、金十数据、Alpha Vantage、CoinDesk 等，含情感方向和重要性评分
   - importance 越高越重要，优先关注 importance > 0.7 的事件

3. **市场状态（Market）**：来自 Binance / Yahoo Finance 的实时行情，含 RSI/MA/ATR 等技术指标
   - 数据客观可靠，但技术指标仅为参考，不是决策依据

4. **资金信号（Flow）**：来自 Binance Futures 的资金费率/OI/多空比，反映市场情绪
   - 极端值（如资金费率过高）通常意味着情绪过热，需注意反转风险

## 融合原则
- 私域高质量观点 > 实时数据信号 > 技术指标
- 相互印证的信号权重加倍，相互矛盾的信号需在叙述中说明分歧
- 上下文信息是精选后的最相关内容，不是全量数据，请充分利用每条信息
- **setup_quality 评级标准**（严格按此执行）：
  - insufficient：完全没有任何可用数据（无市场数据、无事件、无观点、无新闻）
  - low：只有少量单一来源数据（如只有市场行情，无事件/观点）
  - medium：有市场数据 + 新闻事件或观点，来源基本多元
  - high：有私域高质量观点 + 市场数据 + 事件，信号相互印证
- 只要有市场行情数据或新闻事件，setup_quality 至少为 low，绝不输出 insufficient
- conviction 低（如 0.3）可以，但 setup_quality 必须如实反映数据丰富程度

## 输出格式（JSON）
- direction: bullish|bearish|neutral
- conviction: 0.0-1.0（确信度，信息充分且一致才给高值）
- setup_quality: high|medium|low|insufficient
- key_supporting_factors: 主要支撑因素列表（最多5条，注明来源类型）
- key_risk_factors: 主要风险因素列表（最多3条）
- synthesis_narrative: 综合叙述（150字以内，需说明主要依据来源）
- recommended_action: enter|watch|avoid|hold
- time_horizon: immediate|short|medium|long

返回纯 JSON，不要包含其他文字。
"""


class FusionInput(BaseModel):
    asset: str
    question: Optional[str] = None
    claims: List[dict] = []
    events: List[dict] = []
    market_state: Optional[dict] = None
    flow_signals: List[dict] = []
    data_coverage: Optional[dict] = None  # 私域数据覆盖摘要，告知 LLM 本地有哪些数据


class FusionOutput(BaseModel):
    asset: str
    direction: str
    conviction: float
    setup_quality: str
    key_supporting_factors: List[str] = []
    key_risk_factors: List[str] = []
    synthesis_narrative: str
    recommended_action: str
    time_horizon: str
    is_mock: bool = False
    error: Optional[str] = None


class FusionSkill:
    def execute(self, input: FusionInput) -> FusionOutput:
        """融合多源信号"""
        # 如果没有任何信号，直接返回 insufficient
        if not input.claims and not input.events and not input.market_state and not input.flow_signals:
            return FusionOutput(
                asset=input.asset,
                direction="neutral",
                conviction=0.0,
                setup_quality="insufficient",
                synthesis_narrative="信号数量不足，无法生成有效判断",
                recommended_action="watch",
                time_horizon="unspecified",
                error="Insufficient signals",
            )

        # 量化信号预评分（规则引擎，在 LLM 之前执行）
        from app.services.signal_scorer import compute_scorecard, scorecard_to_prompt_section
        scorecard = compute_scorecard(
            asset=input.asset,
            claims=input.claims or [],
            market=input.market_state,
            flow_signals=input.flow_signals or [],
            events=input.events or [],
        )

        user_prompt = self._build_prompt(input)
        # 将量化评分注入 prompt
        user_prompt += scorecard_to_prompt_section(scorecard)
        _response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "fusion_output",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                        "conviction": {"type": "number"},
                        "setup_quality": {"type": "string", "enum": ["high", "medium", "low", "insufficient"]},
                        "key_supporting_factors": {"type": "array", "items": {"type": "string"}},
                        "key_risk_factors": {"type": "array", "items": {"type": "string"}},
                        "synthesis_narrative": {"type": "string"},
                        "recommended_action": {"type": "string", "enum": ["enter", "watch", "avoid", "hold"]},
                        "time_horizon": {"type": "string", "enum": ["immediate", "short", "medium", "long"]},
                    },
                    "required": ["direction", "conviction", "setup_quality",
                                 "key_supporting_factors", "key_risk_factors",
                                 "synthesis_narrative", "recommended_action", "time_horizon"],
                    "additionalProperties": False,
                },
            },
        }
        try:
            response_text = llm_client.complete_with_role_str(
                skill_role="fusion",
                system_prompt=FUSION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1500,
                temperature=0.2,
                response_format=_response_format,
            )
            return self._parse_response(input.asset, response_text, input.claims or [], input=input)
        except Exception as e:
            return FusionOutput(
                asset=input.asset,
                direction="neutral",
                conviction=0.0,
                setup_quality="insufficient",
                synthesis_narrative="融合分析出错",
                recommended_action="watch",
                time_horizon="unspecified",
                error=str(e),
            )

    def _build_prompt(self, input: FusionInput) -> str:
        from datetime import datetime
        import zoneinfo
        _tz = zoneinfo.ZoneInfo('Asia/Shanghai')
        _now_str = datetime.now(_tz).strftime('%Y年%m月%d日 %H:%M:%S CST')
        parts = [f"当前时间：{_now_str}（请以此为基准判断所有观点和数据的时效性）", f"资产：{input.asset}"]
        if input.question:
            parts.append(f"研究问题：{input.question}")

        # 数据覆盖摘要：告知 LLM 本地有哪些私域数据，避免幻觉
        if input.data_coverage:
            cov = input.data_coverage
            total = cov.get("total_active_claims", 0)
            dr = cov.get("date_range", {})
            oldest = dr.get("oldest", "未知")
            newest = dr.get("newest", "未知")
            stance_dist = cov.get("stance_distribution", {})
            stance_str = "、".join(f"{k}:{v}" for k, v in stance_dist.items()) if stance_dist else "无"
            parts.append(
                f"\n【本地私域数据覆盖（请基于此判断信息充分程度）】"
                f"\n- 活跃观点总数：{total} 条"
                f"\n- 数据日期范围：{oldest} ~ {newest}"
                f"\n- 观点立场分布：{stance_str}"
                f"\n注：下方私域观点为混合召回（语义相似 + 资产匹配 + 高信任保底），"
                f"已从 {total} 条中精选最相关的，非全量。"
            )

        if input.claims:
            parts.append(f"\n【私域观点 {len(input.claims)} 条（已按相关性+可信度排序）】")
            for c in input.claims:
                stance = c.get("stance", "")
                summary = c.get("summary", "")
                tw = c.get("trust_weight", 0.5)
                conf = c.get("confidence", 0.5)
                speaker = c.get("speaker_name") or "匿名"
                horizon = c.get("time_horizon") or ""
                contradicted = "⚠️已被反驳" if c.get("is_contradicted") else ""
                paths = "/".join(c.get("_retrieval_paths", []))
                quote = c.get("full_quote", "")
                line = (
                    f"- [{stance}|信任{tw:.1f}|置信{conf:.1f}] "
                    f"{summary}（来源：{speaker}{', '+horizon if horizon else ''}）"
                    f"{' ' + contradicted if contradicted else ''}"
                )
                if quote and len(quote) < 120:
                    line += f"\n  原文：\"{quote}\""
                parts.append(line)

        if input.events:
            parts.append(f"\n【近期事件 {len(input.events)} 条】")
            for e in input.events[:3]:
                imp = e.get("importance", 0)
                title = e.get("title", "")
                direction = e.get("impact_direction", "")
                parts.append(f"- [重要性{imp:.1f}|{direction}] {title}")

        if input.market_state:
            ms = input.market_state
            parts.append(f"\n【市场状态】")
            parts.append(f"- 价格：{ms.get('price')}，趋势：{ms.get('trend_direction')}")
            parts.append(f"- RSI：{ms.get('rsi_14')}，波动：{ms.get('volatility_state')}")
            if ms.get("structure_notes"):
                parts.append(f"- 结构：{ms.get('structure_notes')}")

        if input.flow_signals:
            parts.append(f"\n【资金信号 {len(input.flow_signals)} 条】")
            for f_ in input.flow_signals[:3]:
                direction = f_.get("direction", "")
                desc = f_.get("description", "")
                parts.append(f"- [{direction}] {desc}")

        parts.append("\n请输出综合判断（JSON 格式）：")
        return "\n".join(parts)

    def _parse_response(self, asset: str, response_text: str, claims: list = [], input: 'FusionInput | None' = None) -> FusionOutput:
        try:
            data = robust_json_parse(response_text, context="FusionSkill")
            is_mock = data.pop("_mock", False)
            setup_quality = data.get("setup_quality", "low")
            # 安全网：只要有市场数据或事件，就不允许 LLM 输出 insufficient
            if setup_quality == "insufficient" and input is not None:
                has_data = bool(input.market_state) or bool(input.events) or bool(input.flow_signals)
                if has_data:
                    setup_quality = "low"
            return FusionOutput(
                asset=asset,
                direction=data.get("direction", "neutral"),
                conviction=float(data.get("conviction", 0.5)),
                setup_quality=setup_quality,
                key_supporting_factors=data.get("key_supporting_factors") or [
                    # fallback：从 claims 自动构造，避免 LLM 漏填
                    f"{c.get('summary','')[:60]}" for c in claims[:3] if c.get('stance') in ('bullish', 'positive')
                ] or ([data.get("synthesis_narrative", "")] if data.get("synthesis_narrative") else []),
                key_risk_factors=data.get("key_risk_factors") or [],
                synthesis_narrative=data.get("synthesis_narrative", ""),
                recommended_action=data.get("recommended_action", "watch"),
                time_horizon=data.get("time_horizon", "unspecified"),
                is_mock=is_mock,
            )
        except Exception as e:
            return FusionOutput(
                asset=asset,
                direction="neutral",
                conviction=0.0,
                setup_quality="insufficient",
                synthesis_narrative="解析失败",
                recommended_action="watch",
                time_horizon="unspecified",
                error=f"Parse error: {e}",
            )
