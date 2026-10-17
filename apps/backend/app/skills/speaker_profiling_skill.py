"""Speaker Profiling Skill

职责：从 RawSource 中识别发言者，更新或创建 SpeakerProfile。
"""
import json
from typing import Optional
from pydantic import BaseModel
from app.utils.llm_client import llm_client
from app.utils.json_repair import robust_json_parse


SPEAKER_SYSTEM_PROMPT = """你是一位专业的信息来源分析师。
根据文章内容和 speaker_hint，识别并分析发言者的背景。

输出 JSON 格式：
- name: 发言者姓名（字符串）
- aliases: 别名列表（如有）
- affiliation: 所属机构/公司
- role: 职位/角色
- domain_tags: 专业领域标签列表（如 ["宏观", "加密", "美股"]）
- style_notes: 分析风格描述（50字以内）
- trust_weight: 初始可信度权重 0.0-1.0（默认0.5，知名分析师可适当提高）

规则：
- 如果无法确定发言者，name 返回 speaker_hint 原文
- domain_tags 最多 5 个
- 不要猜测没有证据支持的信息
"""


class SpeakerProfilingInput(BaseModel):
    raw_source_id: str
    text_content: str
    speaker_hint: Optional[str] = None
    theme_hint: Optional[str] = None


class SpeakerProfilingOutput(BaseModel):
    name: str
    aliases: list[str] = []
    affiliation: Optional[str] = None
    role: Optional[str] = None
    domain_tags: list[str] = []
    style_notes: Optional[str] = None
    trust_weight: float = 0.5
    is_mock: bool = False
    error: Optional[str] = None


class SpeakerProfilingSkill:
    def execute(self, input: SpeakerProfilingInput) -> SpeakerProfilingOutput:
        """识别并分析发言者"""
        if not input.speaker_hint and not input.text_content:
            return SpeakerProfilingOutput(
                name="Unknown",
                error="No speaker hint or content provided",
            )

        user_prompt = self._build_prompt(input)
        try:
            response_text = llm_client.complete_with_role_str(
                skill_role="speaker",
                system_prompt=SPEAKER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=800,
                temperature=0.1,
            )
            return self._parse_response(response_text, input.speaker_hint)
        except Exception as e:
            # fallback：用 speaker_hint 作为最小信息
            return SpeakerProfilingOutput(
                name=input.speaker_hint or "Unknown",
                domain_tags=self._guess_domains(input.theme_hint),
                error=str(e),
            )

    def _build_prompt(self, input: SpeakerProfilingInput) -> str:
        parts = []
        if input.speaker_hint:
            parts.append(f"发言者提示：{input.speaker_hint}")
        if input.theme_hint:
            parts.append(f"主题标签：{input.theme_hint}")
        # 只取前 500 字节避免 token 超限
        snippet = input.text_content[:500] if input.text_content else ""
        parts.append(f"\n文章摘录（前500字）：\n{snippet}")
        parts.append("\n请根据以上信息分析发言者，输出 JSON：")
        return "\n".join(parts)

    def _parse_response(
        self, response_text: str, fallback_name: Optional[str]
    ) -> SpeakerProfilingOutput:
        try:
            data = robust_json_parse(response_text, context="SpeakerProfiling")
            is_mock = data.pop("_mock", False)
            trust = max(0.0, min(1.0, float(data.get("trust_weight", 0.5))))
            return SpeakerProfilingOutput(
                name=data.get("name") or fallback_name or "Unknown",
                aliases=data.get("aliases", []),
                affiliation=data.get("affiliation"),
                role=data.get("role"),
                domain_tags=data.get("domain_tags", [])[:5],
                style_notes=data.get("style_notes"),
                trust_weight=trust,
                is_mock=is_mock,
            )
        except Exception as e:
            return SpeakerProfilingOutput(
                name=fallback_name or "Unknown",
                error=f"Parse error: {e}",
            )

    @staticmethod
    def _guess_domains(theme_hint: Optional[str]) -> list[str]:
        """从 theme_hint 快速推断领域标签"""
        if not theme_hint:
            return []
        keywords = {
            "宏观": "宏观", "macro": "宏观",
            "加密": "加密", "crypto": "加密", "btc": "加密", "eth": "加密",
            "美股": "美股", "equity": "美股", "stock": "美股",
            "黄金": "大宗商品", "gold": "大宗商品", "commodity": "大宗商品",
            "债券": "固定收益", "bond": "固定收益",
            "策略": "策略",
        }
        hint_lower = theme_hint.lower()
        found = []
        for kw, tag in keywords.items():
            if kw in hint_lower and tag not in found:
                found.append(tag)
        return found[:5]
