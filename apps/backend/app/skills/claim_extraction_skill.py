"""Claim Extraction Skill

职责：从 RawSource 的文本内容中抽取结构化 ClaimCard。

规则：
- Skill 本身不写数据库
- 只返回结构化结果
- Workflow 负责持久化
"""
import json
from typing import Optional, List, Literal
from pydantic import BaseModel, field_validator, model_validator
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse

# ── 合法枚举值 ──────────────────────────────────────────────
VALID_STANCES = {"bullish", "bearish", "neutral", "conditional"}
VALID_CLAIM_TYPES = {
    "macro_view", "sector_view", "asset_view",
    "strategy_rule", "risk_warning", "data_observation",
    "trading_pattern", "market_narrative",
}
VALID_HORIZONS = {"immediate", "short", "medium", "long", "unspecified"}

# LLM 常见非标准输出 → 标准值映射
STANCE_ALIASES = {
    "positive": "bullish", "negative": "bearish", "mixed": "conditional",
    "uncertain": "neutral", "cautious": "neutral", "optimistic": "bullish",
    "pessimistic": "bearish",
}
HORIZON_ALIASES = {
    "near-term": "short", "near_term": "short", "short-term": "short",
    "medium-term": "medium", "mid-term": "medium", "long-term": "long",
    "longer-term": "long", "now": "immediate", "unknown": "unspecified",
}


CLAIM_EXTRACTION_SYSTEM_PROMPT = """你是一位专业的金融研究分析师。
你的任务是从给定的文本中，精确抽取所有有价值的观点（Claims）。

文本来源可能是多种类型：专业文章、研究报告、群聊记录、视频文字稿、个人笔记等。
请根据内容本身的价值来抽取，不要因为来源形式不够"正式"就忽略有价值的观点。

特别注意：
- 群聊记录中可能有多位发言者，请分别抽取每个人的独立观点
- 口语化表达也可能包含有价值的判断，请提炼其核心含义
- 如果内容涉及市场参与者的信念、文化因素或群体行为模式（如"某类投资者倾向于..."），这些也是有价值的观点，请归类为 market_narrative
- 交易策略、仓位管理、入场/出场规则等内容请归类为 trading_pattern

每个 Claim 必须包含：
- summary: 观点的简洁摘要（50字以内，可适当延伸以保留核心逻辑）
- contextual_summary: **局部上下文**（50-100 字，重要）
  说明该观点在原文中的定位信息，便于后续检索还原语境。需要涵盖：
  1. 来源类型（专业报告 / 群聊记录 / 视频字幕 / 社交短文）
  2. 发表时间（如可从文本/提示推断）
  3. 发言人或机构（从 speaker_hint 、原文提取）
  4. 原文主话题（什么背景下讨论）
  5. 该观点的归属（谁的判断 / 引用谁的研究 / 谁的走势预期）
  示例：“该观点出自 2025-09-15 《X 财经》访谈节目，是分析师张三在讨论
  美联储 9 月利率路径时，针对黄金中期走势给出的判断。”
  不要抓‘原文原话’，而是提炼授予该观点“上下文坐标”的元信息。
- full_quote: **原文引用**（从原文中逐字复制支撑该观点的关键段落，30-200字，保留原始表述和上下文）
  - 这是最重要的字段之一，必须填写，不要留空
  - **必须是原文的连续片段，逐字复制，不得改写、重组或跨段拼接**
  - 对于群聊记录，只引用同一条消息内的文字，不要把不同消息拼在一起
  - 对于文章/报告，引用原文中的关键段落

  ✅ 正确示例（群聊）—— 引用单条消息的原文：
    原文: "大卫李：我觉得BTC现在是底部区域，55000就是铁底"
    full_quote: "大卫李：我觉得BTC现在是底部区域，55000就是铁底"

  ❌ 错误示例（群聊）—— 跨消息拼接、用省略号串联、改写措辞：
    full_quote: "大卫李：BTC是底部...55000铁底...FOMC之后再看"  ← 拼接了多条消息
    full_quote: "大卫李认为BTC处于底部区域，铁底为55000"  ← 改写了原文措辞

  ✅ 正确示例（文章）—— 逐字复制一个连续段落：
    full_quote: "美国的赤字规模已经超过了历史上任何一个和平时期，利率水平与债务负担之间的矛盾日益尖锐。"

  ❌ 错误示例（文章）—— 摘要式改写：
    full_quote: "美国赤字超历史水平，利率与债务矛盾加剧"  ← 缩写了原文
- claim_type: 必须是以下之一: macro_view|sector_view|asset_view|strategy_rule|risk_warning|data_observation|trading_pattern|market_narrative
  - trading_pattern: 具体的交易模式/策略/仓位管理方法
  - market_narrative: 市场叙事、群体信念、文化因素驱动的行为模式
- stance: 必须是以下之一: bullish|bearish|neutral|conditional
- time_horizon: 必须是以下之一: immediate|short|medium|long|unspecified
- time_sensitive: true 或 false（关键字段！）
  判断规则：
  - **false**（长期有效）：该观点是理念、思路、方法论、分析框架、市场叙事、泛化认知等抽象内容。
    例："美联储降息周期一般利好风险资产"、"期权卖方在高波动时收益更好"、"CTA 策略适合趋势市场"。
    这类内容不需要过期，长期有效。
  - **true**（有时间窗口）：该观点涉及具体的价格水平、时间节点、数据发布、事件触发等。
    例："三天后关注 VIX 是否突破 20"、"如果石油价格不破 20 日线可以入手"、"本周五非农数据公布前注意仓位"。
    这类内容一旦时间窗口过去就失效。
- expiry_hint: 仅当 time_sensitive=true 时填写，用自然语言描述何时失效。
  例："三天后"、"非农数据公布后"、"本周五收盘"、"FOMC 决议后 3 天"。
  如果 time_sensitive=false，留空字符串 ""。
- asset_scope: 涉及的资产/标的标签列表。
  重要规则：
  1. 同一资产必须同时写中文和英文，例如石油写成 ["石油", "OIL", "原油"]，比特币写成 ["比特币", "BTC", "Bitcoin"]
  2. 包含所有相关资产，不限于标准代码
  3. 宏观主题也纳入，例如 ["美联储", "Fed", "经济周期", "通胀", "inflation"]
  4. 可为空列表
- theme_scope: 涉及的主题/概念标签列表，如 ["流动性", "liquidity", "通胀", "inflation", "OPEC"]，同样中英文并列
- confidence: 0.0-1.0 之间的小数，观点的明确程度
  - 对于主观信念或叙事类内容，请根据论证的完整性而非"是否可量化验证"来评估
- reasoning_basis: 观点的依据列表，可为空列表
- conditions: 观点成立的前提条件列表，可为空列表
- invalidation_conditions: 观点失效的条件列表，可为空列表

注意：
1. 只抽取文中明确表达的观点，不要推断或添加
2. 每个独立的观点单独记录
3. 严格返回 JSON 格式：{"claims": [...]}
4. 如果文本中没有明确观点，返回 {"claims": []}
5. 枚举值必须严格使用上述规定的值，不要使用其他词汇
6. 不要因为观点来自非正式场合（群聊、口语）就降低 confidence，应基于观点本身的逻辑完整性
7. full_quote 必须是原文中一段**连续的、逐字复制**的文字。禁止：改写措辞、用省略号拼接、跨段落/跨消息合并。如果一个观点跨越多条消息，只引用最核心的那一条
"""


class ExtractedClaim(BaseModel):
    summary: str
    # G17 Contextual Embeddings：LLM 同次生成的 50-100 字局部上下文
    # 供 semantic_search 在 embed 前拼接到 chunk 前面，提升检索质量
    # 老 LLM 输出不含该字段时 → None → retrieval 退化为 summary+full_quote 老路径
    contextual_summary: Optional[str] = None
    claim_type: str = "macro_view"
    stance: str = "neutral"
    time_horizon: str = "unspecified"
    asset_scope: List[str] = []
    theme_scope: List[str] = []
    confidence: float = 0.8
    reasoning_basis: List[str] = []
    conditions: List[str] = []
    invalidation_conditions: List[str] = []
    full_quote: Optional[str] = None
    time_sensitive: bool = True
    expiry_hint: str = ""
    _warnings: List[str] = []

    @field_validator("stance", mode="before")
    @classmethod
    def normalize_stance(cls, v: str) -> str:
        v = str(v).lower().strip()
        if v in VALID_STANCES:
            return v
        if v in STANCE_ALIASES:
            return STANCE_ALIASES[v]
        return "neutral"  # 兜底

    @field_validator("claim_type", mode="before")
    @classmethod
    def normalize_claim_type(cls, v: str) -> str:
        v = str(v).lower().strip().replace("-", "_").replace(" ", "_")
        if v in VALID_CLAIM_TYPES:
            return v
        return "macro_view"  # 兜底

    @field_validator("time_horizon", mode="before")
    @classmethod
    def normalize_horizon(cls, v: str) -> str:
        v = str(v).lower().strip()
        if v in VALID_HORIZONS:
            return v
        if v in HORIZON_ALIASES:
            return HORIZON_ALIASES[v]
        return "unspecified"  # 兜底

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v) -> float:
        try:
            f = float(v)
            return max(0.0, min(1.0, f))
        except (TypeError, ValueError):
            return 0.8

    @field_validator("asset_scope", "theme_scope", "reasoning_basis",
                     "conditions", "invalidation_conditions", mode="before")
    @classmethod
    def ensure_list(cls, v) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, list):
            return [str(item) for item in v if item]
        return []

    @field_validator("summary", mode="before")
    @classmethod
    def ensure_summary(cls, v: str) -> str:
        v = str(v).strip()
        if len(v) > 200:
            v = v[:200]
        return v or "[无摘要]"

    @field_validator("contextual_summary", mode="before")
    @classmethod
    def normalize_contextual_summary(cls, v) -> Optional[str]:
        """允许 LLM 返回 None / 空串 / 过长串；超过 400 字裁切（防幻觉）。"""
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        if len(s) > 400:
            s = s[:400]
        return s


class ClaimExtractionInput(BaseModel):
    raw_source_id: str
    text_content: str
    speaker_hint: Optional[str] = None
    theme_hint: Optional[str] = None
    source_type_hint: Optional[str] = None  # article|video|tweet|chat|note 等
    max_claims: int = 10


class ClaimExtractionOutput(BaseModel):
    raw_source_id: str
    claims: List[ExtractedClaim]
    total_extracted: int
    is_mock: bool = False
    skipped_count: int = 0  # 因校验失败跳过的数量
    warnings: List[str] = []
    error: Optional[str] = None
    source_mode: str = "live"  # G2: 标记数据来源模式


class ClaimExtractionSkill:
    def execute(self, input: ClaimExtractionInput) -> ClaimExtractionOutput:
        """从文本抽取 Claim"""
        if not input.text_content or len(input.text_content.strip()) < 20:
            return ClaimExtractionOutput(
                raw_source_id=input.raw_source_id,
                claims=[],
                total_extracted=0,
                error="Text content too short",
            )

        user_prompt = self._build_prompt(input)

        # 根据文本长度动态调整 max_tokens（长文本可能抽出更多观点）
        text_len = len(input.text_content)
        max_tokens = 3000 if text_len < 4000 else 4000 if text_len < 8000 else 6000

        try:
            response_text = llm_client.complete_with_role_str(
                skill_role="extraction",
                system_prompt=CLAIM_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=0.1,
            )
            return self._parse_response(input.raw_source_id, response_text)
        except Exception as e:
            return ClaimExtractionOutput(
                raw_source_id=input.raw_source_id,
                claims=[],
                total_extracted=0,
                error=str(e),
            )

    # 每 2000 字约可抽取 3-5 条观点
    _CLAIMS_PER_2K_CHARS = 5

    # 来源类型的中文描述映射
    _SOURCE_TYPE_LABELS = {
        "article": "专业文章/分析报告",
        "video": "视频文字稿",
        "tweet": "社交媒体/短文（可能很短，只抽取有明确观点和依据的内容，忽略纯闲聊/转发/情绪宣泄）",
        "chat": "群聊记录（可能包含多位发言者）",
        "note": "社交媒体笔记（可能含图文，只关注有实质性判断的内容）",
        "social": "社交媒体内容（只抽取有实质性观点的内容，忽略纯闲聊/转发）",
        "report": "研究报告",
    }

    def _build_prompt(self, input: ClaimExtractionInput) -> str:
        parts = []
        # 告知 LLM 来源类型，让它调整抽取策略
        if input.source_type_hint:
            label = self._SOURCE_TYPE_LABELS.get(input.source_type_hint, input.source_type_hint)
            parts.append(f"文本类型：{label}")
        if input.speaker_hint:
            parts.append(f"来源：{input.speaker_hint}")
        if input.theme_hint:
            parts.append(f"主题提示：{input.theme_hint}")
        # 全量送入文本（已由 workflow 预切块，通常 ≤ 8000 字）
        parts.append(f"\n文本内容：\n{input.text_content}")
        # 根据文本长度动态调整最大观点数
        dynamic_max = max(input.max_claims, len(input.text_content) // 2000 * self._CLAIMS_PER_2K_CHARS)
        dynamic_max = min(dynamic_max, 30)  # 上限 30 条
        parts.append(f"\n请抽取最多 {dynamic_max} 个观点，严格返回 JSON 格式。")
        return "\n".join(parts)

    def _parse_response(self, source_id: str, response_text: str) -> ClaimExtractionOutput:
        """解析并严格校验 LLM 响应"""
        warnings: List[str] = []
        skipped = 0

        try:
            data = robust_json_parse(response_text, context="ClaimExtraction")
        except (json.JSONDecodeError, IndexError) as e:
            return ClaimExtractionOutput(
                raw_source_id=source_id,
                claims=[],
                total_extracted=0,
                error=f"JSON parse failed: {e}. Raw: {response_text[:300]}",
            )

        is_mock = data.get("_mock", False) or data.get("mock", False)

        if "claims" not in data:
            return ClaimExtractionOutput(
                raw_source_id=source_id,
                claims=[],
                total_extracted=0,
                error=f"No 'claims' key in response (keys={list(data.keys())}): {str(data)[:200]}",
                is_mock=is_mock,
            )

        raw_claims = data["claims"]

        if not isinstance(raw_claims, list):
            return ClaimExtractionOutput(
                raw_source_id=source_id,
                claims=[],
                total_extracted=0,
                error="LLM response 'claims' field is not a list",
                is_mock=is_mock,
            )

        claims: List[ExtractedClaim] = []
        for i, raw in enumerate(raw_claims):
            if not isinstance(raw, dict):
                skipped += 1
                warnings.append(f"Claim[{i}] skipped: not a dict")
                continue
            if not raw.get("summary", "").strip():
                skipped += 1
                warnings.append(f"Claim[{i}] skipped: empty summary")
                continue
            try:
                claim = ExtractedClaim(**raw)
                claims.append(claim)
            except Exception as e:
                skipped += 1
                warnings.append(f"Claim[{i}] skipped: validation error — {e}")

        return ClaimExtractionOutput(
            raw_source_id=source_id,
            claims=claims,
            total_extracted=len(claims),
            is_mock=is_mock,
            skipped_count=skipped,
            warnings=warnings,
            source_mode="mock" if is_mock else "live",
        )
