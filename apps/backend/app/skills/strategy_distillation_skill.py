"""Strategy Distillation Skill

职责：从 RawSource 文本中抽取结构化策略规则，生成 StrategyCard。

规则：
- 只抽取明确的、可操作的策略规则
- Skill 不写数据库，返回结构化结果
- Workflow 负责持久化
"""
import json
from typing import Optional, List
from pydantic import BaseModel, field_validator
from app.utils.json_repair import robust_json_parse
from app.utils.llm_client import llm_client

VALID_STRATEGY_TYPES = {
    "trend_following", "mean_reversion", "breakout",
    "event_driven", "macro_rotation", "carry", "arbitrage", "custom"
}
VALID_HORIZONS = {"immediate", "short", "medium", "long"}

STRATEGY_DISTILLATION_SYSTEM_PROMPT = """你是一位专业的量化策略研究员。
你的任务是从给定的金融文章/报告/访谈文本中，提炼出明确的、可操作的交易策略规则。

策略必须包含：
- strategy_name: 策略名称（20字以内）
- strategy_type: 必须是以下之一: trend_following|mean_reversion|breakout|event_driven|macro_rotation|carry|arbitrage|custom
- summary: 策略简介（50字以内）
- core_logic: 策略核心逻辑（100字以内）
- applicable_assets: 适用资产列表，如 ["BTC", "SPX"]
- applicable_horizons: 适用周期列表，如 ["short", "medium"]
- applicable_regimes: 适用市场状态，如 ["trending", "high_volatility"]
- entry_conditions: 入场条件列表（每条30字以内，最多5条）
- disqualifying_conditions: 否决条件列表（每条30字以内，最多3条）
- exit_rules: 退出规则列表（每条30字以内，最多3条）
- risk_factors: 风险因素列表（每条30字以内，最多3条）
- confidence: 0.0-1.0，策略规则的明确程度

注意：
1. 只抽取文中明确表达的可操作规则，不要推断
2. 每套独立策略单独记录
3. 严格返回 JSON 格式：{"strategies": [...]}
4. 如果没有明确策略规则，返回 {"strategies": []}
"""


class DistilledStrategy(BaseModel):
    strategy_name: str
    strategy_type: str = "custom"
    summary: str
    core_logic: str
    applicable_assets: List[str] = []
    applicable_horizons: List[str] = []
    applicable_regimes: List[str] = []
    entry_conditions: List[str] = []
    disqualifying_conditions: List[str] = []
    exit_rules: List[str] = []
    risk_factors: List[str] = []
    confidence: float = 0.7

    @field_validator("strategy_type", mode="before")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        v = str(v).lower().strip().replace("-", "_").replace(" ", "_")
        return v if v in VALID_STRATEGY_TYPES else "custom"

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.7

    @field_validator(
        "applicable_assets", "applicable_horizons", "applicable_regimes",
        "entry_conditions", "disqualifying_conditions", "exit_rules", "risk_factors",
        mode="before"
    )
    @classmethod
    def ensure_list(cls, v) -> List[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return [str(i) for i in v if i]


class StrategyDistillationInput(BaseModel):
    raw_source_id: str
    text_content: str
    speaker_hint: Optional[str] = None
    max_strategies: int = 3


class StrategyDistillationOutput(BaseModel):
    raw_source_id: str
    strategies: List[DistilledStrategy]
    total_extracted: int
    skipped_count: int = 0
    warnings: List[str] = []
    is_mock: bool = False
    source_mode: str = "live"
    error: Optional[str] = None


class StrategyDistillationSkill:
    def execute(self, input: StrategyDistillationInput) -> StrategyDistillationOutput:
        """从文本抽取策略规则"""
        if not input.text_content or len(input.text_content.strip()) < 50:
            return StrategyDistillationOutput(
                raw_source_id=input.raw_source_id,
                strategies=[], total_extracted=0,
                error="Text content too short for strategy distillation",
            )

        user_prompt = self._build_prompt(input)
        try:
            response_text = llm_client.complete_with_role_str(
                skill_role="strategy",
                system_prompt=STRATEGY_DISTILLATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=3000,
                temperature=0.15,
            )
            return self._parse_response(input.raw_source_id, response_text)
        except Exception as e:
            return StrategyDistillationOutput(
                raw_source_id=input.raw_source_id,
                strategies=[], total_extracted=0, error=str(e),
            )

    def _build_prompt(self, input: StrategyDistillationInput) -> str:
        parts = []
        if input.speaker_hint:
            parts.append(f"来源：{input.speaker_hint}")
        parts.append(f"\n文本内容：\n{input.text_content[:4000]}")
        parts.append(f"\n请从上述文本中提炼最多 {input.max_strategies} 套策略规则，严格返回 JSON 格式。")
        return "\n".join(parts)

    def _parse_response(
        self, source_id: str, response_text: str
    ) -> StrategyDistillationOutput:
        warnings: List[str] = []
        skipped = 0
        try:
            data = robust_json_parse(response_text, context="StrategyDistillation")
        except (json.JSONDecodeError, IndexError) as e:
            return StrategyDistillationOutput(
                raw_source_id=source_id, strategies=[], total_extracted=0,
                error=f"JSON parse failed: {e}. Raw: {response_text[:300]}",
            )

        is_mock = data.get("_mock", False)
        raw_list = data.get("strategies", [])
        if not isinstance(raw_list, list):
            return StrategyDistillationOutput(
                raw_source_id=source_id, strategies=[], total_extracted=0,
                error="'strategies' field is not a list", is_mock=is_mock,
            )

        strategies: List[DistilledStrategy] = []
        for i, raw in enumerate(raw_list):
            if not isinstance(raw, dict):
                skipped += 1; warnings.append(f"Strategy[{i}] skipped: not a dict")
                continue
            if not raw.get("strategy_name", "").strip():
                skipped += 1; warnings.append(f"Strategy[{i}] skipped: empty name")
                continue
            try:
                strategies.append(DistilledStrategy(**raw))
            except Exception as e:
                skipped += 1
                warnings.append(f"Strategy[{i}] skipped: {e}")

        return StrategyDistillationOutput(
            raw_source_id=source_id,
            strategies=strategies,
            total_extracted=len(strategies),
            skipped_count=skipped,
            warnings=warnings,
            is_mock=is_mock,
            source_mode="mock" if is_mock else "live",
        )
