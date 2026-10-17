"""Research Plan Skill

阶段一：AI 分析用户问题，输出结构化研究计划（需要哪些数据、初步假设）
"""
import json
import re
from typing import List, Optional
from pydantic import BaseModel
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse


RESEARCH_PLAN_SYSTEM_PROMPT = """你是一位专业的量化研究分析师助手。
用户会给你一个市场研究问题，你的任务是：

1. **识别研究标的**：明确要研究的资产/市场
2. **列出所需数据**：列出回答这个问题需要哪些具体数据（价格、技术指标、资金费率、宏观数据、新闻事件等）
3. **初步假设**：基于你的训练知识给出初步方向判断和核心假设
4. **关键问题**：列出需要实时数据才能回答的核心问题

输出严格 JSON 格式：
{
  "asset": "BTC",  // 主要研究标的符号（BTC/ETH/OIL/GOLD/SPX/DXY/NDX/VIX 等），若问题与具体资产无关则填 "MACRO"
  "is_open_ended": false,  // true=开放式宏观/经济问题，无需锚定单一资产
  "secondary_assets": ["DXY", "GOLD"],  // 相关联的资产（用于交叉验证），可为空数组
  "data_needs": {
    "market_data": true,     // 是否需要价格/技术指标
    "flow_signals": true,    // 是否需要资金流/期货持仓/资金费率
    "news_events": true,     // 是否需要最新新闻/事件
    "macro_data": false,     // 是否需要宏观数据（利率/CPI/美债收益率等）
    "cross_asset": false     // 是否需要跨资产数据
  },
  "preliminary_direction": "bullish",  // bullish/bearish/neutral/uncertain
  "preliminary_conviction": 0.3,       // 0.0-1.0，基于训练知识的初步确信度
  "key_hypotheses": [
    "假设一：基于训练知识的初步判断"
  ],
  "open_questions": [
    "需要实时数据才能回答的问题，如：当前价格是否站上200日均线？"
  ],
  "reasoning": "初步分析逻辑（2-3句话）"
}

直接返回 JSON，不要有其他文字。
"""


class DataNeeds(BaseModel):
    market_data: bool = True
    flow_signals: bool = False
    news_events: bool = True
    macro_data: bool = False
    cross_asset: bool = False


class ResearchPlan(BaseModel):
    asset: str
    is_open_ended: bool = False
    secondary_assets: List[str] = []
    data_needs: DataNeeds = DataNeeds()
    preliminary_direction: str = "uncertain"
    preliminary_conviction: float = 0.3
    key_hypotheses: List[str] = []
    open_questions: List[str] = []
    reasoning: str = ""
    model_used: str = ""


class ResearchPlanSkill:
    def execute(self, question: str, history: list = []) -> ResearchPlan:
        from datetime import datetime
        import zoneinfo
        tz_cst = zoneinfo.ZoneInfo('Asia/Shanghai')
        today = datetime.now(tz_cst).strftime('%Y年%m月%d日 %H:%M CST')

        user_prompt = f"当前日期：{today}\n\n用户研究问题：{question}"
        if history:
            recent = history[-4:]
            h_text = "\n".join(
                f"{'用户' if h.get('role')=='user' else '助手'}：{str(h.get('content',''))[:200]}"
                for h in recent
            )
            user_prompt = f"对话历史：\n{h_text}\n\n---\n{user_prompt}"

        try:
            response_text, model_used = llm_client.complete_with_role(
                skill_role="fusion",
                system_prompt=RESEARCH_PLAN_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1024,
                temperature=0.3,
            )
            data = self._parse(response_text)
            plan = ResearchPlan(**data)
            plan.model_used = model_used
            return plan
        except Exception as e:
            from loguru import logger
            logger.warning(f"[ResearchPlanSkill] failed: {e}")
            # fallback: 从问题中猜资产
            asset = self._guess_asset(question)
            is_open = asset == 'MACRO'
            return ResearchPlan(
                asset=asset,
                is_open_ended=is_open,
                data_needs=DataNeeds(market_data=not is_open, flow_signals=not is_open, news_events=True, macro_data=is_open),
                preliminary_direction="uncertain",
                preliminary_conviction=0.2,
                key_hypotheses=[],
                open_questions=["当前价格水平？", "近期资金流向？"] if not is_open else ["当前经济周期阶段？", "主要宏观风险？"],
                reasoning="计划生成失败，使用默认数据需求。",
                model_used="fallback",
            )

    def _parse(self, text: str) -> dict:
        data = robust_json_parse(text, context="ResearchPlan")
        # 确保 data_needs 是 DataNeeds 兼容的 dict
        if isinstance(data.get('data_needs'), dict):
            data['data_needs'] = DataNeeds(**data['data_needs'])
        return data

    def _guess_asset(self, question: str) -> str:
        """A1：优先用 canonical detect_asset；无匹配时按宏观关键词回落到 MACRO。"""
        from app.utils.asset_alias import detect_assets
        hits = detect_assets(question)
        if hits:
            return hits[0]
        q = question.lower()
        macro_keywords = ['经济周期', '通胀', '衰退', '利率', '美联储', 'fed', 'cpi', 'gdp',
                          '货币政策', '财政政策', '宏观', 'macro', '周期', 'cycle',
                          '就业', '失业', '美债', '收益率', 'yield']
        if any(k in q for k in macro_keywords):
            return 'MACRO'
        return 'BTC'
