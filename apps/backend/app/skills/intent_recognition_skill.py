"""Intent Recognition Skill

职责：识别用户问题的意图，输出结构化意图信息，指导后续 workflow 路由。

双层策略：
  第一层（规则层）：关键词快速匹配，0ms，无 API 消耗
  第二层（LLM 层）：规则层置信度不足时触发，精细识别

输出字段：
  mode:                    路由模式
  assets:                  涉及资产（含别名展开）
  themes:                  主题关键词
  time_horizon:            时间范围
  needs_private_knowledge: 是否需要召回私域 Claim
  needs_debate:            是否需要委员会辩论
  confidence:              置信度（规则层低于阈值时触发 LLM）
"""
import json
import re
from typing import List, Literal, Optional
from pydantic import BaseModel
from loguru import logger
from app.utils.json_repair import robust_json_parse


# ── 输出模型 ──────────────────────────────────────────────────
class Intent(BaseModel):
    mode: Literal["asset_analysis", "macro", "debate", "knowledge_query", "chat",
                  "replay", "scout", "audit"] = "chat"
    assets: List[str] = []          # 已识别的资产标签
    themes: List[str] = []          # 主题关键词
    time_horizon: Literal["immediate", "short", "medium", "long", "unspecified"] = "unspecified"
    needs_private_knowledge: bool = False
    needs_debate: bool = False
    confidence: float = 0.5
    source: Literal["rules", "llm", "fallback"] = "fallback"


# ── 规则层配置（A1：统一从 asset_alias 导入，避免多处重复维护）──
from app.utils.asset_alias import ASSET_ALIASES as ASSET_KEYWORDS  # noqa: F401

DEBATE_KEYWORDS = [
    "怎么看", "多空", "辩论", "分歧", "争议", "多方", "空方",
    "看多", "看空", "反驳", "对立", "两面",
    "bull", "bear", "debate", "pros and cons",
]

PRIVATE_KNOWLEDGE_KEYWORDS = [
    "我的", "我上传", "知识库", "我存的", "历史", "之前", "记录",
    "观点", "分析师", "报告",
]

HORIZON_KEYWORDS: dict[str, list[str]] = {
    "immediate": ["今天", "今日", "现在", "立即", "马上", "today", "intraday"],
    "short":     ["短期", "本周", "近期", "这周", "short", "near term", "week"],
    "medium":    ["中期", "中线", "季度", "几个月", "medium", "quarter"],
    "long":      ["长期", "长线", "年", "long term", "year"],
}

MACRO_KEYWORDS = [
    "宏观", "经济", "通胀", "利率", "美联储", "Fed", "货币政策",
    "GDP", "就业", "OPEC", "地缘", "周期", "macro", "recession",
]

# V2: 回放 / 审计 / 发现 关键词
REPLAY_KEYWORDS = [
    "回放", "复盘", "反思", "复盘分析", "当时的判断",
    "决策回顾", "replay", "review decision", "postmortem",
]

AUDIT_KEYWORDS = [
    "验证", "核实", "真假", "真伪", "可信度", "证据审计",
    "来源可靠", "信源", "verify", "audit", "fact check",
]

SCOUT_KEYWORDS = [
    "雷达", "发现", "机会", "异动", "今日发现", "扩缩",
    "比较", "扫描", "radar", "scout", "opportunity", "scan",
]

# 规则层置信度低于此阈值时触发 LLM
LLM_THRESHOLD = 0.65


# ── LLM 识别提示词 ────────────────────────────────────────────
INTENT_SYSTEM_PROMPT = """你是一个金融问答系统的意图识别模块。
分析用户问题，输出结构化意图 JSON，字段如下：

- mode: 以下之一
    asset_analysis  — 针对特定资产的行情分析/价格判断
    macro           — 宏观经济/政策/周期分析，不针对单一资产
    debate          — 需要多方辩证分析，存在明显多空分歧
    knowledge_query — 查询用户私域知识库/上传的资料
    replay          — 决策回放/复盘反思，回顾历史判断
    scout           — 主动发现/雷达扫描，寻找潜在机会
    audit           — 证据审计/真伪核实，检查信息可靠性
    chat            — 一般性问答/解释概念

- assets: 涉及的资产代码列表，如 ["BTC", "OIL"]，未涉及具体资产则为 []
- themes: 主题关键词列表，如 ["通胀", "流动性", "OPEC减产"]
- time_horizon: immediate|short|medium|long|unspecified
- needs_private_knowledge: true/false — 是否需要查询私域知识库
- needs_debate: true/false — 是否适合启动多方辩论

严格返回 JSON，不要任何其他文字。"""


class IntentRecognitionSkill:
    """双层意图识别：规则层快速判断 + LLM 精细识别。"""

    def recognize(self, question: str, force_llm: bool = False, rule_only: bool = False) -> Intent:
        """识别用户问题意图。

        Args:
            question:  用户输入的问题文本
            force_llm: 强制使用 LLM 层（忽略规则层置信度）
            rule_only: 仅返回规则层结果；适用于用户已手动选择工作流的执行入口

        Returns:
            Intent 对象
        """
        if not question or not question.strip():
            return Intent(mode="chat", source="fallback")

        # 第一层：规则快速判断
        rule_intent = self._rule_layer(question)

        if rule_only:
            logger.debug(f"[Intent] rule-only: mode={rule_intent.mode} conf={rule_intent.confidence:.2f}")
            return rule_intent

        if not force_llm and rule_intent.confidence >= LLM_THRESHOLD:
            logger.debug(f"[Intent] rules: mode={rule_intent.mode} conf={rule_intent.confidence:.2f}")
            return rule_intent

        # 第二层：LLM 精细识别
        try:
            llm_intent = self._llm_layer(question)
            logger.debug(f"[Intent] llm: mode={llm_intent.mode} assets={llm_intent.assets}")
            # 融合：LLM 为主，规则层补充 assets（防止 LLM 漏掉明确资产词）
            merged_assets = list({*llm_intent.assets, *rule_intent.assets})
            llm_intent.assets = merged_assets
            return llm_intent
        except Exception as e:
            logger.warning(f"[Intent] LLM layer failed: {e}, using rule result")
            return rule_intent

    def _rule_layer(self, question: str) -> Intent:
        """规则层：关键词匹配，返回初步意图和置信度。"""
        q_lower = question.lower()
        confidence = 0.4  # 基础置信度
        assets: list[str] = []
        themes: list[str] = []
        mode = "chat"
        needs_debate = False
        needs_private = False
        time_horizon = "unspecified"

        # 资产识别
        for kw, asset in ASSET_KEYWORDS.items():
            if kw in q_lower or kw in question:
                if asset not in assets:
                    assets.append(asset)
        if assets:
            mode = "asset_analysis"
            confidence += 0.2

        # 宏观意图
        macro_hits = [kw for kw in MACRO_KEYWORDS if kw.lower() in q_lower]
        if macro_hits:
            themes.extend(macro_hits[:3])
            if not assets:  # 无具体资产时判定为宏观
                mode = "macro"
            confidence += 0.1

        # 辩论意图
        if any(kw.lower() in q_lower for kw in DEBATE_KEYWORDS):
            needs_debate = True
            if assets:
                mode = "debate"
            confidence += 0.15

        # 私域知识
        if any(kw in question for kw in PRIVATE_KNOWLEDGE_KEYWORDS):
            needs_private = True
            if mode == "chat":
                mode = "knowledge_query"
            confidence += 0.15

        # 时间范围
        for horizon, kws in HORIZON_KEYWORDS.items():
            if any(kw.lower() in q_lower for kw in kws):
                time_horizon = horizon  # type: ignore
                confidence += 0.05
                break

        # V2: 回放意图
        if any(kw.lower() in q_lower for kw in REPLAY_KEYWORDS):
            mode = "replay"
            confidence += 0.2

        # V2: 审计意图
        if any(kw.lower() in q_lower for kw in AUDIT_KEYWORDS):
            if mode == "chat":
                mode = "audit"
            confidence += 0.15

        # V2: 发现/雷达意图
        if any(kw.lower() in q_lower for kw in SCOUT_KEYWORDS):
            if mode == "chat":
                mode = "scout"
            confidence += 0.15

        # 有资产且有分析词时提升置信度
        analysis_words = ["走势", "价格", "分析", "判断", "预测", "outlook", "price", "trend"]
        if assets and any(w in q_lower for w in analysis_words):
            confidence += 0.1

        return Intent(
            mode=mode,  # type: ignore
            assets=assets,
            themes=themes,
            time_horizon=time_horizon,  # type: ignore
            needs_private_knowledge=needs_private,
            needs_debate=needs_debate,
            confidence=min(confidence, 1.0),
            source="rules",
        )

    def _llm_layer(self, question: str) -> Intent:
        """LLM 层：精细识别，返回高置信度意图。"""
        from app.utils.llm_client import llm_client
        resp = llm_client.complete_with_role_str(
            skill_role="fusion",   # 使用国产模型（快速、低成本）
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_prompt=f"用户问题：{question}",
            max_tokens=300,
            temperature=0.1,
        )
        data = robust_json_parse(resp, context="IntentRecognition")

        # 验证 mode
        valid_modes = {"asset_analysis", "macro", "debate", "knowledge_query", "chat",
                       "replay", "scout", "audit"}
        mode = data.get("mode", "chat")
        if mode not in valid_modes:
            mode = "chat"

        # 验证 time_horizon
        valid_horizons = {"immediate", "short", "medium", "long", "unspecified"}
        horizon = data.get("time_horizon", "unspecified")
        if horizon not in valid_horizons:
            horizon = "unspecified"

        return Intent(
            mode=mode,  # type: ignore
            assets=[str(a).upper() for a in (data.get("assets") or [])],
            themes=[str(t) for t in (data.get("themes") or [])],
            time_horizon=horizon,  # type: ignore
            needs_private_knowledge=bool(data.get("needs_private_knowledge", False)),
            needs_debate=bool(data.get("needs_debate", False)),
            confidence=0.9,
            source="llm",
        )


# 全局单例
intent_skill = IntentRecognitionSkill()
