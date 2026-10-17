"""Mode Router — 三模式融合调度决策器

职责：根据用户消息 + Intent 结果，决定执行 chat / briefing / debate 中的哪一个，
并产出人类可读的决策理由、预估成本、可升/降级路径。

设计原则：
  - 显式 > 隐式：斜杠命令 / 强制路由始终最高优先级
  - 默认降级：歧义时走 chat（成本最低，保证永远有答案）
  - 决策可见：每次 auto 路由都产出 reason，供 UI 展示
  - 摩擦校准：昂贵动作（debate）需要明确触发信号

路由优先级（从高到低）：
  0. force_route（调用方指定）
  1. 斜杠命令 /briefing /debate /chat（消息前缀）
  2. needs_debate=True + intent.confidence >= 0.7        → debate
  3. mode="debate"    + intent.confidence >= 0.7         → debate
  4. 决策词（重仓/加仓/买入等）+ 有资产                   → briefing
  5. mode in (asset_analysis, macro, scout) + conf >= 0.7 → briefing
  6. 深度关键词（深度/研报/详细分析）                    → briefing
  7. 其他所有（含 knowledge_query / 低置信）             → chat
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.skills.intent_recognition_skill import Intent


ROUTE_CHAT = "chat"
ROUTE_BRIEFING = "briefing"
ROUTE_DEBATE = "debate"
VALID_ROUTES = (ROUTE_CHAT, ROUTE_BRIEFING, ROUTE_DEBATE)


# ══════════════════════════════════════════════════════════════════════
# 关键词配置
# ══════════════════════════════════════════════════════════════════════

# 决策词：用户想要做实际交易决策 → 升级为 briefing
DECISION_KEYWORDS = [
    "重仓", "加仓", "减仓", "满仓", "清仓", "建仓",
    "买入", "卖出", "开仓", "平仓", "抄底", "逃顶",
    "止损", "止盈", "仓位", "该不该买", "该不该卖", "要不要买", "要不要卖",
    "entry", "exit", "long it", "short it", "buy the dip",
]

# 深度关键词：用户明确想要详细/研报式回答 → briefing
DEEP_KEYWORDS = [
    "深度", "详细", "研报", "研究报告", "全面分析", "深入", "完整分析",
    "详细分析", "深度分析", "deep dive", "thorough",
]

# 斜杠命令（消息前缀）
SLASH_COMMANDS = {
    "/chat": ROUTE_CHAT,
    "/briefing": ROUTE_BRIEFING,
    "/brief": ROUTE_BRIEFING,
    "/research": ROUTE_BRIEFING,
    "/debate": ROUTE_DEBATE,
    "/committee": ROUTE_DEBATE,
}

# 决策置信度阈值：intent.confidence 低于此值时降级到 chat
INTENT_CONFIDENCE_FLOOR = 0.7

# 成本预估（USD，基于当前模型价格均值；实际范围可能 ±50%）
COST_ESTIMATE = {
    ROUTE_CHAT: 0.002,
    ROUTE_BRIEFING: 0.08,
    ROUTE_DEBATE: 0.50,
}

# 路由升级路径（Chat → Briefing → Debate）
UPGRADE_PATH: dict[str, List[str]] = {
    ROUTE_CHAT: [ROUTE_BRIEFING, ROUTE_DEBATE],
    ROUTE_BRIEFING: [ROUTE_DEBATE],
    ROUTE_DEBATE: [],
}


# ══════════════════════════════════════════════════════════════════════
# 数据类
# ══════════════════════════════════════════════════════════════════════

@dataclass
class RoutingDecision:
    """单次路由决策的完整信息，供后端执行 + 前端展示"""
    route: str                              # chat | briefing | debate
    reason: str                             # 人类可读的决策理由
    confidence: float                       # 本次决策的置信度
    intent_mode: str                        # intent_skill 给出的 mode
    intent_confidence: float                # intent 自身的 confidence
    assets: List[str] = field(default_factory=list)
    force_source: Optional[str] = None      # 'slash' / 'api' / None
    cleaned_message: str = ""               # 去除斜杠前缀后的纯净消息
    cost_estimate_usd: float = 0.0          # 预估成本
    can_upgrade_to: List[str] = field(default_factory=list)
    can_downgrade_to: Optional[str] = None

    def to_payload(self) -> dict:
        """可直接存入 ChatMessage.payload 的字典形式"""
        return {
            "route": self.route,
            "reason": self.reason,
            "confidence": self.confidence,
            "intent_mode": self.intent_mode,
            "intent_confidence": self.intent_confidence,
            "assets": self.assets,
            "force_source": self.force_source,
            "cost_estimate_usd": self.cost_estimate_usd,
            "can_upgrade_to": self.can_upgrade_to,
            "can_downgrade_to": self.can_downgrade_to,
        }


# ══════════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════════

def parse_slash_command(message: str) -> tuple[Optional[str], str]:
    """解析消息前缀的斜杠命令。

    返回 (route, cleaned_message)。若无命令，返回 (None, original_message)。
    """
    msg = (message or "").lstrip()
    if not msg.startswith("/"):
        return None, message
    # 取首个 token
    m = re.match(r"^(/\w+)\s*", msg)
    if not m:
        return None, message
    cmd = m.group(1).lower()
    route = SLASH_COMMANDS.get(cmd)
    if not route:
        return None, message
    cleaned = msg[m.end():].strip()
    return route, cleaned


def contains_any(text: str, keywords: List[str]) -> Optional[str]:
    """返回首个命中的关键词，否则 None"""
    t = (text or "").lower()
    for kw in keywords:
        if kw.lower() in t:
            return kw
    return None


def _build_upgrade_info(route: str) -> tuple[List[str], Optional[str]]:
    """基于当前路由，计算可升级路径与可降级路径"""
    upgrade = UPGRADE_PATH.get(route, [])
    if route == ROUTE_BRIEFING:
        downgrade = ROUTE_CHAT
    elif route == ROUTE_DEBATE:
        downgrade = ROUTE_BRIEFING
    else:
        downgrade = None
    return upgrade, downgrade


# ══════════════════════════════════════════════════════════════════════
# 主决策入口
# ══════════════════════════════════════════════════════════════════════

def decide_route(
    message: str,
    intent: Intent,
    force_route: Optional[str] = None,
) -> RoutingDecision:
    """核心决策函数。

    Args:
        message:     用户原始消息
        intent:      intent_skill.recognize(message) 的结果
        force_route: 调用方显式指定路由（API 层转发 UI 的强制选择）

    Returns:
        RoutingDecision
    """
    # ── 0. 调用方强制（来自 UI 的显式 mode 按钮，非 auto）
    if force_route and force_route in VALID_ROUTES:
        up, down = _build_upgrade_info(force_route)
        return RoutingDecision(
            route=force_route,
            reason=f"用户显式指定 {force_route} 模式",
            confidence=1.0,
            intent_mode=intent.mode,
            intent_confidence=intent.confidence,
            assets=intent.assets,
            force_source="api",
            cleaned_message=message,
            cost_estimate_usd=COST_ESTIMATE[force_route],
            can_upgrade_to=up,
            can_downgrade_to=down,
        )

    # ── 1. 斜杠命令
    slash_route, cleaned = parse_slash_command(message)
    if slash_route:
        up, down = _build_upgrade_info(slash_route)
        return RoutingDecision(
            route=slash_route,
            reason=f"检测到斜杠命令 → 强制 {slash_route}",
            confidence=1.0,
            intent_mode=intent.mode,
            intent_confidence=intent.confidence,
            assets=intent.assets,
            force_source="slash",
            cleaned_message=cleaned or message,
            cost_estimate_usd=COST_ESTIMATE[slash_route],
            can_upgrade_to=up,
            can_downgrade_to=down,
        )

    # 2-7 使用原 message（无斜杠）
    # ── 2. 明确辩论意图
    if intent.needs_debate and intent.confidence >= INTENT_CONFIDENCE_FLOOR:
        up, down = _build_upgrade_info(ROUTE_DEBATE)
        return RoutingDecision(
            route=ROUTE_DEBATE,
            reason=f"检测到多空/辩论意图（intent.confidence={intent.confidence:.2f}）",
            confidence=intent.confidence,
            intent_mode=intent.mode,
            intent_confidence=intent.confidence,
            assets=intent.assets,
            cleaned_message=message,
            cost_estimate_usd=COST_ESTIMATE[ROUTE_DEBATE],
            can_upgrade_to=up,
            can_downgrade_to=down,
        )

    if intent.mode == "debate" and intent.confidence >= INTENT_CONFIDENCE_FLOOR:
        up, down = _build_upgrade_info(ROUTE_DEBATE)
        return RoutingDecision(
            route=ROUTE_DEBATE,
            reason=f"意图识别 mode=debate（confidence={intent.confidence:.2f}）",
            confidence=intent.confidence,
            intent_mode=intent.mode,
            intent_confidence=intent.confidence,
            assets=intent.assets,
            cleaned_message=message,
            cost_estimate_usd=COST_ESTIMATE[ROUTE_DEBATE],
            can_upgrade_to=up,
            can_downgrade_to=down,
        )

    # ── 3. 决策词 + 有资产 → briefing（单边实际决策需要深度）
    decision_kw = contains_any(message, DECISION_KEYWORDS)
    if decision_kw and intent.assets:
        up, down = _build_upgrade_info(ROUTE_BRIEFING)
        return RoutingDecision(
            route=ROUTE_BRIEFING,
            reason=f"检测到决策词「{decision_kw}」+ 资产 {intent.assets[0]}，已升级为深度研究",
            confidence=0.85,
            intent_mode=intent.mode,
            intent_confidence=intent.confidence,
            assets=intent.assets,
            cleaned_message=message,
            cost_estimate_usd=COST_ESTIMATE[ROUTE_BRIEFING],
            can_upgrade_to=up,
            can_downgrade_to=down,
        )

    # ── 4. 深度关键词 → briefing
    deep_kw = contains_any(message, DEEP_KEYWORDS)
    if deep_kw:
        up, down = _build_upgrade_info(ROUTE_BRIEFING)
        return RoutingDecision(
            route=ROUTE_BRIEFING,
            reason=f"检测到深度研究关键词「{deep_kw}」",
            confidence=0.8,
            intent_mode=intent.mode,
            intent_confidence=intent.confidence,
            assets=intent.assets,
            cleaned_message=message,
            cost_estimate_usd=COST_ESTIMATE[ROUTE_BRIEFING],
            can_upgrade_to=up,
            can_downgrade_to=down,
        )

    # ── 5. 资产/宏观/发现意图 + 高置信 → briefing
    if (
        intent.mode in ("asset_analysis", "macro", "scout")
        and intent.confidence >= INTENT_CONFIDENCE_FLOOR
    ):
        up, down = _build_upgrade_info(ROUTE_BRIEFING)
        return RoutingDecision(
            route=ROUTE_BRIEFING,
            reason=f"意图识别 mode={intent.mode}（confidence={intent.confidence:.2f}）",
            confidence=intent.confidence,
            intent_mode=intent.mode,
            intent_confidence=intent.confidence,
            assets=intent.assets,
            cleaned_message=message,
            cost_estimate_usd=COST_ESTIMATE[ROUTE_BRIEFING],
            can_upgrade_to=up,
            can_downgrade_to=down,
        )

    # ── 6. 兜底 → chat（默认降级，保证总有答案）
    up, down = _build_upgrade_info(ROUTE_CHAT)
    reason_bits = [f"意图 mode={intent.mode}"]
    if intent.confidence < INTENT_CONFIDENCE_FLOOR:
        reason_bits.append(f"置信度偏低 {intent.confidence:.2f}")
    reason_bits.append("默认走快速问答")
    return RoutingDecision(
        route=ROUTE_CHAT,
        reason=" · ".join(reason_bits),
        confidence=max(0.5, intent.confidence),
        intent_mode=intent.mode,
        intent_confidence=intent.confidence,
        assets=intent.assets,
        cleaned_message=message,
        cost_estimate_usd=COST_ESTIMATE[ROUTE_CHAT],
        can_upgrade_to=up,
        can_downgrade_to=down,
    )


__all__ = [
    "decide_route",
    "parse_slash_command",
    "RoutingDecision",
    "ROUTE_CHAT",
    "ROUTE_BRIEFING",
    "ROUTE_DEBATE",
    "VALID_ROUTES",
    "COST_ESTIMATE",
    "UPGRADE_PATH",
]
