"""Prompt Adapter — 模型自适应 Prompt 优化层（2026-04-06 更新）

解耦 Prompt 与特定模型的强关联：
- 根据模型型号（而非仅家族）自动调整 Prompt 策略
- 在 System Prompt 末尾追加模型特有的输出指令
- 调整温度建议

模型特性摘要（基于 OpenRouter 模型页 + Arena 榜 2026-03-31）：
- Claude Opus 4.6: Arena #1/#2, 长链条推理旗舰, $5/$25, 200K ctx
- Claude Sonnet 4.6: Arena #19, 稳健 workhorse, $3/$15, 1M ctx
- Gemini 3.1 Pro Preview: Arena #3, AA 57, 推理+agent 可靠, $2/$12, 1M ctx
- Gemini 3 Flash Preview: Arena #18, 接近 Pro 推理, $0.50/$3, 1M ctx
- GPT-5.4: Arena #6~#17, 反证/找漏洞, $2.50/$15, 1M ctx
- GLM-5: Arena #22, BFCL v4 #2 (70.8%), 审计/schema 校验, $0.72/$2.30
- DeepSeek V3.2: SWE-Rebench #4, 便宜 worker, $0.26/$0.38, 164K ctx

温度设置来源标注：
- [官方] = OpenRouter/模型提供方推荐
- [经验] = 基于同类任务的通用经验值
- [待验] = 需要实测验证的推测值
"""
from typing import Optional, Tuple


# ── 模型家族检测 ─────────────────────────────────────────────
def detect_model_family(model: str) -> str:
    """从模型标识符推断模型家族"""
    m = model.lower()
    if "claude" in m or "anthropic" in m:
        return "claude"
    if "gemini" in m or "google" in m:
        return "gemini"
    if "gpt" in m or "openai" in m or "o1" in m or "o3" in m or "o4" in m:
        return "gpt"
    if "deepseek" in m:
        return "deepseek"
    if "qwen" in m:
        return "qwen"
    if "glm" in m or "zhipu" in m or "z-ai" in m:
        return "glm"
    if "mimo" in m or "xiaomi" in m:
        return "mimo"
    return "unknown"


def _detect_model_tier(model: str) -> str:
    """检测具体模型型号层级，用于更细粒度的适配"""
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "3.1-pro" in m:
        return "gemini_pro"
    if "3-flash" in m or "3.1-flash" in m:
        return "gemini_flash"
    if "flash-lite" in m:
        return "gemini_lite"
    if "gpt-5.4-nano" in m:
        return "gpt_nano"
    if "gpt-5.4-mini" in m:
        return "gpt_mini"
    if "gpt-5.4" in m:
        return "gpt_full"
    if "glm-5" in m:
        return "glm5"
    if "deepseek-v3" in m:
        return "deepseek_v3"
    if "mimo-v2-pro" in m:
        return "mimo_pro"
    if "mimo" in m:
        return "mimo_flash"
    return "unknown"


# ── 按具体模型型号的输出指令 ─────────────────────────────────
# 每个指令都标注了为什么这样设计

_MODEL_SUFFIXES = {
    # Claude Opus 4.6 — 旗舰推理模型，Arena #1/#2
    # 特性：超强推理链、指令遵从度极高，但偶尔过度展开
    "claude": (
        "\n\n[输出约束]"
        "\n- 所有字段值必须完整填写，不要省略或用 ... 代替"
        "\n- 最终只输出 JSON 结果，不要在 JSON 前后添加解释"
        "\n- 列表字段如果确实没有内容可以为空数组 []"
    ),
    # Gemini 3.1 Pro — Arena #3, AA 57, 强量化推理
    # 已知问题：有时会嵌套额外 wrapper 对象，列表可能返回对象数组而非字符串数组
    "gemini": (
        "\n\n[输出约束]"
        "\n- JSON 输出必须是扁平的单层对象，绝对不要嵌套额外的 wrapper 对象"
        "\n- 不要在 JSON 外添加任何 markdown 格式或解释文字"
        "\n- 列表字段（如 key_arguments）直接使用字符串数组 [\"...\"]，不要用对象数组"
        "\n- 数值字段（如 confidence）必须是数字而非字符串"
    ),
    # GPT-5.4 — Arena #6~#17, 强格式遵从 + 函数调用
    # 特性：system prompt 遵从度高，适合做反证和找漏洞
    "gpt": (
        "\n\n[输出约束]"
        "\n- 严格遵守 system prompt 中的输出格式要求"
        "\n- JSON 所有必填字段都有值，数值字段使用数字而非字符串"
        "\n- 不要添加额外的解释或 markdown 代码块包裹"
    ),
    # GLM-5 — BFCL v4 #2 (70.8%), SWE-Rebench #2 (62.8%)
    # 特性：审计/schema 校验强，中文优秀，需控制输出长度
    "glm": (
        "\n\n[输出约束]"
        "\n- 请用中文回答分析内容，JSON 字段名保持英文"
        "\n- 严格按照要求的 JSON schema 输出，不添加多余字段"
        "\n- 保持输出精炼，避免冗长展开"
        "\n- 数值型字段必须是合法范围内的数字"
    ),
    # DeepSeek V3.2 — SWE-Rebench #4, 极低成本 worker
    # 特性：中文好、成本极低，但复杂格式偶尔丢字段
    "deepseek": (
        "\n\n[输出约束]"
        "\n- 请用中文回答，JSON 字段名保持英文"
        "\n- JSON 结构尽量简洁，每个字段都必须填写（不能为 null 除非明确允许）"
        "\n- 列表字段至少包含一个元素"
        "\n- 数值型字段（如 confidence）必须是 0.0-1.0 之间的小数"
    ),
    "qwen": (
        "\n\n[输出约束]"
        "\n- 请用中文回答，JSON 字段名保持英文"
        "\n- 严格按照要求的 JSON 格式输出，不添加额外字段"
        "\n- 确保所有数值在合法范围内"
    ),
    # MiMo-V2-Flash — 309B MoE (15B active), SWE-bench 开源 #1
    # 特性：hybrid-thinking toggle, 结构化输出稳定，但中文金融场景经验较少
    "mimo": (
        "\n\n[输出约束]"
        "\n- 请用中文回答分析内容，JSON 字段名保持英文"
        "\n- 严格按照 JSON schema 输出，不要包裹 markdown 代码块"
        "\n- 所有必填字段都必须有值，数值字段使用数字而非字符串"
        "\n- 保持简洁，不要在 JSON 前后添加解释"
    ),
}

# ── 温度建议矩阵 ─────────────────────────────────────────────
# 格式: (skill_category, model_family) → (温度, 来源标注)
# 来源: [经验] = 通用经验值, [官方] = 模型方建议, [待验] = 推测值
_TEMP_MATRIX = {
    # 提取类：需要精确格式，低温
    ("extraction", "deepseek"): (0.05, "[经验] DeepSeek 提取需极低温确保格式"),
    ("extraction", "glm"): (0.05, "[经验] GLM 结构化提取极低温"),
    ("extraction", "claude"): (0.10, "[经验] Claude 提取低温"),
    ("extraction", "gemini"): (0.10, "[经验] Gemini 提取低温"),
    ("extraction", "gpt"): (0.10, "[经验] GPT 提取低温"),
    ("extraction", "mimo"): (0.05, "[待验] MiMo Flash 提取极低温"),
    # 融合类：需要一致性但也要综合能力
    ("fusion", "gemini"): (0.15, "[待验] Gemini Pro 融合稍低温提高一致性"),
    ("fusion", "claude"): (0.15, "[经验] Claude 融合低温"),
    # 委员会辩论：需要独立思考和适度创造性
    ("committee", "claude"): (0.30, "[待验] Opus/Sonnet 辩论适度创造性"),
    ("committee", "gemini"): (0.25, "[待验] Gemini Pro 辩论稍保守"),
    ("committee", "gpt"): (0.35, "[待验] GPT-5.4 反证需适度发散"),
    ("committee", "glm"): (0.20, "[待验] GLM-5 结构分析偏稳"),
    # 简报：需要清晰有序的长文输出
    ("briefing", "claude"): (0.25, "[经验] Sonnet 简报清晰稳定"),
    ("briefing", "gemini"): (0.20, "[待验] Gemini Pro 简报"),
    # 裁决：需要高度稳定性
    ("adjudication", "claude"): (0.15, "[经验] 裁决需要高稳定性"),
    # 策略：需要深度推理
    ("strategy", "claude"): (0.20, "[待验] Opus 策略深度推理"),
}


def _get_skill_category(skill_role: str) -> str:
    """从 skill_role 推断技能类别"""
    r = skill_role.lower()
    if "committee" in r:
        return "committee"
    if r in ("briefing",):
        return "briefing"
    if r in ("extraction", "speaker", "date_extraction"):
        return "extraction"
    if r in ("fusion",):
        return "fusion"
    if r in ("strategy",):
        return "strategy"
    if r in ("adjudication",):
        return "adjudication"
    return "general"


# ── 公开 API ─────────────────────────────────────────────────

def adapt_prompt(
    system_prompt: str,
    model: str,
    skill_role: Optional[str] = None,
) -> str:
    """根据模型家族在 system prompt 末尾追加适配指令。

    Args:
        system_prompt: 原始 system prompt
        model: 完整模型标识符（如 "anthropic/claude-opus-4.6"）
        skill_role: 技能角色（如 "committee_a", "fusion"）

    Returns:
        适配后的 system prompt
    """
    family = detect_model_family(model)
    suffix = _MODEL_SUFFIXES.get(family, "")
    if suffix:
        return system_prompt + suffix
    return system_prompt


def adapt_temperature(
    base_temp: float,
    model: str,
    skill_role: Optional[str] = None,
) -> float:
    """根据模型和技能类别建议最优温度。

    如果有针对性建议则使用建议值，否则返回原始温度。
    温度来源标注存储在 _TEMP_MATRIX 中，可通过 get_temp_annotation() 查看。
    """
    family = detect_model_family(model)
    category = _get_skill_category(skill_role or "")
    key = (category, family)
    entry = _TEMP_MATRIX.get(key)
    if entry:
        return entry[0]
    return base_temp


def get_temp_annotation(model: str, skill_role: Optional[str] = None) -> str:
    """获取温度设置的来源标注（供调试和审计）"""
    family = detect_model_family(model)
    category = _get_skill_category(skill_role or "")
    key = (category, family)
    entry = _TEMP_MATRIX.get(key)
    if entry:
        return f"temp={entry[0]}, {entry[1]}"
    return f"temp=base_default, [未配置特定温度]"


def get_model_capabilities(model: str) -> dict:
    """返回模型能力标签，供上层逻辑决策使用。
    
    数据来源：OpenRouter 模型页 + Arena 文本榜 2026-03-31 + AA 综合榜
    """
    family = detect_model_family(model)
    tier = _detect_model_tier(model)
    caps = {
        "claude": {
            "reasoning": "strong",
            "json_compliance": "high",
            "chinese": "good",
            "cost": "high" if tier == "opus" else "medium",
            "context_window": "200k" if tier == "opus" else "1M",
            "arena_rank": "#1-2(thinking)" if tier == "opus" else "#19",
            "strengths": ["complex_reasoning", "instruction_following", "nuanced_analysis", "polished_writing"],
            "source": "OpenRouter + Arena 2026-03-31",
        },
        "gemini": {
            "reasoning": "strong",
            "json_compliance": "medium",
            "chinese": "good",
            "cost": "medium" if tier == "gemini_pro" else "low",
            "context_window": "1M",
            "arena_rank": "#3" if tier == "gemini_pro" else "#18",
            "strengths": ["quantitative_analysis", "long_context", "multimodal", "agent_reliability"],
            "source": "OpenRouter + Arena 2026-03-31 + AA Intelligence=57",
        },
        "gpt": {
            "reasoning": "strong",
            "json_compliance": "high",
            "chinese": "good",
            "cost": "high" if tier == "gpt_full" else ("medium" if tier == "gpt_mini" else "low"),
            "context_window": "1M" if tier == "gpt_full" else "400K",
            "arena_rank": "#6(high-effort)/#17" if tier == "gpt_full" else "#23",
            "strengths": ["counterargument", "function_calling", "structured_output", "finding_flaws"],
            "source": "OpenRouter + Arena 2026-03-31",
        },
        "glm": {
            "reasoning": "good",
            "json_compliance": "high",
            "chinese": "excellent",
            "cost": "low",
            "context_window": "128K",
            "arena_rank": "#22",
            "strengths": ["schema_validation", "audit", "function_calling", "judge_tasks"],
            "source": "OpenRouter + BFCL v4 #2(70.8%) + SWE-Rebench #2(62.8%)",
        },
        "deepseek": {
            "reasoning": "good",
            "json_compliance": "medium",
            "chinese": "excellent",
            "cost": "very_low",
            "context_window": "164K",
            "arena_rank": "#56",
            "strengths": ["chinese_nlp", "cost_effective", "iterative_engineering", "agentic_tool_use"],
            "source": "OpenRouter + SWE-Rebench #4(60.9%)",
        },
        "qwen": {
            "reasoning": "good",
            "json_compliance": "good",
            "chinese": "excellent",
            "cost": "low",
            "context_window": "128K",
            "strengths": ["chinese_nlp", "embedding", "structured_extraction"],
            "source": "OpenRouter",
        },
        "mimo": {
            "reasoning": "good",
            "json_compliance": "good",
            "chinese": "good",
            "cost": "very_low" if tier == "mimo_flash" else "medium",
            "context_window": "256K" if tier == "mimo_flash" else "1M",
            "arena_rank": "~#35(Pro)" if tier == "mimo_pro" else "N/A(Flash)",
            "strengths": ["coding", "agent_tasks", "structured_output", "cost_effective"],
            "source": "OpenRouter + SWE-bench Verified 开源 #1 + 309B MoE (15B active)",
        },
    }
    return caps.get(family, {"reasoning": "unknown", "json_compliance": "unknown"})
