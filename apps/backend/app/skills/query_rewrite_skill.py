"""G14 · Query Rewrite Skill (Step-Back Prompting)

短 query（如 "BTC 怎么看"）和带指代的对话（"那它呢？"）embedding 质量都很差，
直接送语义搜索召回率低。该 skill 在 intent 之后、semantic_search 之前调用，
把简短或指代型 query 改写成内容更丰富的检索查询，**不改变** LLM 回答时使用的
原始 user prompt，仅替换检索路径。

核心策略
========
**Step-Back Prompting**：让 LLM 把具体问题"退一步"到更宏观、更可检索的形态。
例：
- "BTC 怎么看"
  → "BTC 当前价格走势、技术指标、市场情绪、宏观影响、风险因素的综合分析"
- "那它呢？"（上下文 ETH）
  → "ETH 当前价格走势、技术指标、市场情绪、宏观影响、风险因素的综合分析"
- "刚才说的会持续多久" + 上下文 BTC
  → "BTC 上涨/下跌趋势的持续时间预测、关键支撑/阻力、催化剂"

只在以下场景触发：
1. **短 query**：字符数 < `QUERY_REWRITE_TRIGGER_MIN_LENGTH`（默认 12）
2. **指代型**：含代词 ``它/这个/那个/上面/刚才/上述/前面提到`` 等
3. 有对话历史 + 短 query 且置信度低

长 query（已经描述充分）不触发，避免引入噪声。

容错
====
- LLM 失败 → fallback 原 query，不阻塞链路
- 改写后过长（> 200 字符）→ 取前 200 字符
- 改写空字符串 → fallback 原 query

环境变量
========
- `ENABLE_QUERY_REWRITE=true`：总开关
- `QUERY_REWRITE_TRIGGER_MIN_LENGTH=12`：短 query 阈值
- `QUERY_REWRITE_HISTORY_TURNS=4`：参考最近 N 轮对话历史
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from loguru import logger

from app.config import settings


# ── 触发规则常量 ────────────────────────────────────────────────────


# 中文指代代词 + 时序指示词（命中即触发 LLM rewrite）
# 注意：Python re 的 `\b` 在中日韩字符之间不视为 word boundary（CJK 都属 \w），
# 所以中文匹配不能加 \b；英文词才需要 \b 防止误伤（如 "fit" 不应匹配 "it"）。
_REFERENTIAL_CN = [
    r"它",
    r"这个",
    r"那个",
    r"上面",
    r"刚才",
    r"上述",
    r"前面(说|提)过?",
    r"先前",
    r"之前",
]
_REFERENTIAL_EN = [
    r"\bthat\b",
    r"\bit\b",
    r"\bthose\b",
    r"\bthese\b",
]

_REFERENTIAL_RE = re.compile(
    "|".join(_REFERENTIAL_CN + _REFERENTIAL_EN),
    re.IGNORECASE,
)


# Step-Back Prompting system prompt
_STEP_BACK_SYSTEM = """你是检索查询重写助手。任务：把用户的简短或带指代的提问改写成更适合\
向量检索的查询字符串。

规则：
1. 把简短问题（如 "BTC 怎么看"）扩展成内容更丰富的检索查询，覆盖：
   价格走势、技术指标、市场情绪、宏观影响、风险因素 等可能相关的方面。
2. 解析对话历史中的指代：把"它/这个/上面/刚才"等替换为对应实体。
3. 保留用户原始的资产代码、时间约束、立场倾向。
4. 改写结果应当是**陈述式查询语句**（不是问句），便于 embedding 匹配文档。
5. 长度控制在 30~150 字符。
6. **只输出改写后的查询字符串**，不要解释、不要 JSON、不要前后缀。

例子：
- 原问题："BTC 怎么看"
  改写："BTC 当前价格走势 技术指标 市场情绪 宏观影响 风险因素 综合分析"
- 原问题："那它呢" 历史含 ETH
  改写："ETH 当前价格走势 技术指标 市场情绪 宏观影响 风险因素 综合分析"
- 原问题："刚才说的会持续多久" 历史含 BTC 上涨
  改写："BTC 上涨趋势持续时间预测 关键支撑阻力 催化剂"
"""


# ── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class RewriteResult:
    """Query rewrite 结果。

    ``rewritten`` 始终非空：触发但失败时回落到原 query。
    ``triggered`` 反映是否真的调用了 LLM；``source`` 标识改写来源。
    """
    original: str
    rewritten: str
    triggered: bool
    source: str  # "rule_skip" | "llm" | "llm_fallback" | "disabled"
    reason: str = ""
    usage: dict[str, Any] | None = None


# ── Skill ──────────────────────────────────────────────────────────


class QueryRewriteSkill:
    """短/指代型 query 改写为更适合检索的扩展查询。"""

    def should_rewrite(self, question: str, history: Optional[List[dict]] = None) -> tuple[bool, str]:
        """判断是否触发 LLM 改写。

        返回 (should_trigger, reason)。
        """
        if not getattr(settings, "ENABLE_QUERY_REWRITE", True):
            return False, "disabled by settings.ENABLE_QUERY_REWRITE"

        q = (question or "").strip()
        if not q:
            return False, "empty question"

        # 1. 含指代代词（无论长短）一律触发
        if history and len(history) > 0 and _REFERENTIAL_RE.search(q):
            return True, "referential pronoun + has history"

        # 2. 短 query 触发（字符数 < 阈值）
        threshold = int(getattr(settings, "QUERY_REWRITE_TRIGGER_MIN_LENGTH", 12))
        if len(q) < threshold:
            return True, f"short query (len={len(q)} < {threshold})"

        return False, f"long enough (len={len(q)})"

    def rewrite(
        self,
        question: str,
        history: Optional[List[dict]] = None,
        max_chars: int = 200,
    ) -> RewriteResult:
        """改写 query；失败 fallback 原始 query。"""
        original = (question or "").strip()
        triggered, reason = self.should_rewrite(original, history)
        if not triggered:
            return RewriteResult(
                original=original,
                rewritten=original,
                triggered=False,
                source="rule_skip",
                reason=reason,
            )

        # 准备 LLM 调用 ── history 取最近 N 轮
        n_turns = int(getattr(settings, "QUERY_REWRITE_HISTORY_TURNS", 4))
        history = history or []
        recent = history[-n_turns * 2:] if n_turns > 0 else []

        # 拼对话历史文本（保持简短）
        history_lines: List[str] = []
        for h in recent:
            role = h.get("role") if isinstance(h, dict) else None
            content = h.get("content") if isinstance(h, dict) else None
            if role in ("user", "assistant") and content:
                speaker = "用户" if role == "user" else "助手"
                # 助手回答可能很长；截 200 字
                history_lines.append(f"{speaker}：{str(content)[:200]}")
        history_text = "\n".join(history_lines) if history_lines else "（无历史）"

        user_prompt = (
            f"## 对话历史\n{history_text}\n\n"
            f"## 当前问题\n{original}\n\n"
            "请输出改写后的检索查询字符串（仅一行，不加引号）："
        )

        try:
            from app.utils.llm_client import llm_client
            text = llm_client.complete_with_role_str(
                skill_role="fusion",  # 与 intent 同分组（快速、低成本）
                system_prompt=_STEP_BACK_SYSTEM,
                user_prompt=user_prompt,
                max_tokens=200,
                temperature=0.3,
            )
            usage = dict(llm_client.get_last_usage() or {})
        except Exception as e:
            logger.warning(f"[QueryRewrite] LLM call failed: {e}; fallback to original")
            return RewriteResult(
                original=original,
                rewritten=original,
                triggered=True,
                source="llm_fallback",
                reason=f"llm error: {str(e)[:80]}",
            )

        rewritten = (text or "").strip()
        # 去掉可能的引号 / 前后缀
        rewritten = rewritten.strip("\"'`")
        # 去除"改写：" / "查询：" 这类前缀
        for prefix in ("改写：", "查询：", "rewrite:", "query:"):
            if rewritten.lower().startswith(prefix.lower()):
                rewritten = rewritten[len(prefix):].strip()
                break
        # 过长 → 截断
        if len(rewritten) > max_chars:
            rewritten = rewritten[:max_chars]
        # 空 → fallback
        if not rewritten:
            return RewriteResult(
                original=original,
                rewritten=original,
                triggered=True,
                source="llm_fallback",
                reason="llm returned empty",
                usage=usage,
            )

        logger.debug(
            f"[QueryRewrite] '{original}' → '{rewritten}' ({reason})"
        )
        return RewriteResult(
            original=original,
            rewritten=rewritten,
            triggered=True,
            source="llm",
            reason=reason,
            usage=usage,
        )


# 全局 singleton
query_rewrite_skill = QueryRewriteSkill()
