"""Speaker Review Skill

定期点评发言人：根据其历史观点，生成画像综述。
包括发言特点、分析方式、擅长领域、投资风格、逻辑稳健性等。
触发时机：每累积 N 条新观点（默认 50）后自动触发，也可手动触发。
"""
import json
import logging
from typing import Optional
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse

logger = logging.getLogger(__name__)

SPEAKER_REVIEW_SYSTEM_PROMPT = """你是一位资深的金融分析师评估专家。
你的任务是根据一位发言人的历史观点记录，对该发言人进行全面画像点评。

你需要评估以下维度（每个维度给出简要点评，2-4句话）：
1. **发言特点**（speaking_style）：语言风格、表达习惯、是否口语化、是否倾向使用数据还是叙事。
2. **分析方式**（analysis_approach）：偏技术分析、基本面、宏观叙事、链上数据、情绪面？混合使用还是单一？
3. **擅长领域**（expertise_domains）：列出该发言人最擅长的2-5个领域，并说明理由。
4. **投资风格**（investment_style）：偏保守/激进？长线/短线？是否有明确的风控意识？
5. **逻辑稳健性**（logic_assessment）：论证是否完整？是否经常跳步或诉诸情绪？
6. **角度独特性**（perspective_uniqueness）：是否经常提供别人忽略的视角？
7. **综合评价**（overall_assessment）：3-5句话的整体评价。
8. **使用建议**（usage_recommendation）：建议如何使用该发言人的观点（如"适合作为启发源"、"可作为反向指标"等）。

输出必须是严格 JSON 格式：
{
  "speaking_style": "...",
  "analysis_approach": "...",
  "expertise_domains": ["领域1", "领域2"],
  "investment_style": "...",
  "logic_assessment": "...",
  "perspective_uniqueness": "...",
  "overall_assessment": "...",
  "usage_recommendation": "...",
  "suggested_trust_weight": 0.0-1.0,
  "suggested_perspective_value": 0.0-1.0,
  "suggested_logic_robustness": 0.0-1.0
}
"""

REVIEW_THRESHOLD = 50  # 每累积多少条新观点触发一次自动点评


async def generate_speaker_review(
    speaker_name: str,
    speaker_aliases: list[str],
    claims_text: str,
    total_claims: int,
    existing_notes: Optional[str] = None,
) -> dict:
    """生成发言人点评。

    Args:
        speaker_name: 发言人姓名
        speaker_aliases: 别名列表
        claims_text: 该发言人所有观点的文本汇总
        total_claims: 总观点数
        existing_notes: 已有的风格笔记（用于增量更新）

    Returns:
        点评结果字典
    """
    aliases_str = f"（别名: {', '.join(speaker_aliases)}）" if speaker_aliases else ""
    existing_str = f"\n\n该发言人此前的画像笔记：\n{existing_notes}" if existing_notes else ""

    user_prompt = f"""以下是发言人 **{speaker_name}**{aliases_str} 的 {total_claims} 条历史观点记录：

{claims_text}
{existing_str}

请根据以上观点记录，对该发言人进行全面画像点评。严格输出 JSON 格式。"""

    try:
        resp = await llm_client(
            messages=[
                {"role": "system", "content": SPEAKER_REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model_role="fusion",
            temperature=0.4,
            max_tokens=2000,
        )
        content = resp if isinstance(resp, str) else resp.get("content", "")
        result = robust_json_parse(content)
        if result:
            return result
        logger.warning(f"[SpeakerReview] Failed to parse JSON for {speaker_name}")
        return {"overall_assessment": content, "error": "json_parse_failed"}
    except Exception as e:
        logger.error(f"[SpeakerReview] Error reviewing {speaker_name}: {e}")
        return {"overall_assessment": f"点评生成失败: {e}", "error": str(e)}


def format_claims_for_review(claims: list, max_chars: int = 15000) -> str:
    """将 claim 列表格式化为点评用文本。"""
    lines = []
    total = 0
    for c in claims:
        line = f"- [{c.get('stance', '?')}] {c.get('summary', '?')}"
        if c.get('full_quote'):
            line += f"\n  原文: {c['full_quote'][:200]}"
        if c.get('asset_scope'):
            line += f"\n  资产: {', '.join(c['asset_scope'][:5])}"
        line += f" (置信度: {c.get('confidence', '?')}, 类型: {c.get('claim_type', '?')})\n"
        total += len(line)
        if total > max_chars:
            lines.append(f"... (剩余 {len(claims) - len(lines)} 条省略)")
            break
        lines.append(line)
    return "\n".join(lines)
