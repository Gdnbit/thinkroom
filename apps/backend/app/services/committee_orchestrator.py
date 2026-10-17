"""Committee Orchestrator — 委员会动态编排服务

三层编排架构：
  第一层：任务模式识别（Task Mode）
  第二层：席位激活（Seat Activation）
  第三层：预算与证据完整度约束（Budget & Evidence Gate）

席位定义（V2）：
  ① Baseline Analyst   → committee_a (Claude Opus)       — V1 已有
  ② Probability Analyst → committee_b (Gemini Pro)        — V1 已有
  ③ Risk Sentinel       → committee_c (GPT-5.4)           — V1 已有
  ④ Structure Analyst   → committee_d (GLM-5)             — V1 已有
  ⑤ Truth/Evidence Audit→ committee_e (DeepSeek V3.2)     — V2 新增
  ⑥ PnL/Execution Advisor→ committee_f (Gemini Flash)    — V2 新增

不是每次全员出动。模型数量可以增加，但必须动态激活。
成本控制的核心不是少用模型，而是少喂无效信息。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from loguru import logger


# ── 席位定义 ────────────────────────────────────────────────────

@dataclass
class SeatConfig:
    """单个席位配置"""
    seat_id: str             # baseline | probability | risk | structure | audit | pnl
    role_label: str          # 中文标签
    skill_role: str          # 对应 LLM client 的 skill_role
    is_v1: bool = True       # V1 已有席位
    extra_token_budget: int = 500  # 角色附件额外 token 预算


SEAT_REGISTRY: Dict[str, SeatConfig] = {
    "baseline": SeatConfig(
        seat_id="baseline", role_label="基准分析师",
        skill_role="committee_a", is_v1=True, extra_token_budget=300,
    ),
    "probability": SeatConfig(
        seat_id="probability", role_label="概率分析师",
        skill_role="committee_b", is_v1=True, extra_token_budget=600,
    ),
    "risk": SeatConfig(
        seat_id="risk", role_label="风控哨兵",
        skill_role="committee_c", is_v1=True, extra_token_budget=500,
    ),
    "structure": SeatConfig(
        seat_id="structure", role_label="市场结构分析师",
        skill_role="committee_d", is_v1=True, extra_token_budget=600,
    ),
    "audit": SeatConfig(
        seat_id="audit", role_label="真伪审计官",
        skill_role="committee_e", is_v1=False, extra_token_budget=400,
    ),
    "pnl": SeatConfig(
        seat_id="pnl", role_label="执行经济性顾问",
        skill_role="committee_f", is_v1=False, extra_token_budget=400,
    ),
}


# ── 任务模式 ────────────────────────────────────────────────────

TASK_MODES = {
    "chat":      {"description": "一般问答", "needs_committee": False},
    "research":  {"description": "研究探索", "needs_committee": True},
    "decision":  {"description": "正式决策", "needs_committee": True},
    "replay":    {"description": "决策回放", "needs_committee": True},
    "scout":     {"description": "主动发现", "needs_committee": False},
    "audit":     {"description": "真伪审计", "needs_committee": True},
}


# ── 席位激活规则 ────────────────────────────────────────────────

# 每种任务模式的默认席位配置
_SEAT_ACTIVATION_RULES: Dict[str, Dict[str, Any]] = {
    "chat": {
        "required": [],
        "optional": [],
    },
    "research": {
        "required": ["baseline"],
        "optional": ["probability", "structure"],
    },
    "decision": {
        "required": ["baseline", "probability", "risk", "structure"],
        "optional": ["audit", "pnl"],
    },
    "replay": {
        "required": ["probability"],
        "optional": ["audit"],
    },
    "scout": {
        "required": [],
        "optional": [],
    },
    "audit": {
        "required": ["audit"],
        "optional": ["baseline"],
    },
}


# ── 编排结果 ────────────────────────────────────────────────────

@dataclass
class OrchestrationPlan:
    """编排器输出的执行计划"""
    task_mode: str
    activated_seats: List[str]
    shared_token_budget: int
    seat_budgets: Dict[str, int]      # seat_id → extra token budget
    total_estimated_tokens: int
    needs_committee: bool
    data_coverage_score: float
    budget_constrained: bool = False   # 是否因预算限制降级了席位
    notes: List[str] = field(default_factory=list)


# ── Token 预算上限 ──────────────────────────────────────────────

_MAX_TOTAL_TOKENS: Dict[str, int] = {
    "chat":      4_000,
    "research":  20_000,
    "decision":  60_000,
    "replay":    15_000,
    "scout":     2_000,
    "audit":     12_000,
}

_SHARED_BASE_TOKENS = 3_000  # 共享共识包基础 token


# ── 编排器主类 ──────────────────────────────────────────────────

class CommitteeOrchestrator:
    """委员会动态编排器

    用法：
        orchestrator = CommitteeOrchestrator()
        plan = orchestrator.plan(
            task_mode="decision",
            asset="BTC",
            data_coverage_score=0.7,
            is_watchlist_asset=True,
            user_force_full=False,
        )
        # plan.activated_seats → ["baseline", "probability", "risk", "structure", "pnl"]
    """

    def plan(
        self,
        task_mode: str,
        asset: str = "",
        data_coverage_score: float = 0.5,
        is_watchlist_asset: bool = True,
        user_force_full: bool = False,
        max_token_override: Optional[int] = None,
        has_unverified_events: bool = False,
    ) -> OrchestrationPlan:
        """生成编排计划

        Args:
            task_mode: 任务模式 (chat/research/decision/replay/scout/audit)
            asset: 目标资产
            data_coverage_score: 数据覆盖完整度 0~1
            is_watchlist_asset: 是否为关注列表中的资产
            user_force_full: 用户是否明确要求完整评估
            max_token_override: 手动覆盖 token 上限
            has_unverified_events: 上下文中是否有未验证事件
        """
        if task_mode not in TASK_MODES:
            task_mode = "chat"

        mode_config = TASK_MODES[task_mode]
        rules = _SEAT_ACTIVATION_RULES.get(task_mode, _SEAT_ACTIVATION_RULES["chat"])
        max_tokens = max_token_override or _MAX_TOTAL_TOKENS.get(task_mode, 20_000)
        notes: List[str] = []

        # ── 第一层：确认是否需要委员会 ──────────────────────────
        if not mode_config["needs_committee"]:
            return OrchestrationPlan(
                task_mode=task_mode,
                activated_seats=[],
                shared_token_budget=max_tokens,
                seat_budgets={},
                total_estimated_tokens=max_tokens,
                needs_committee=False,
                data_coverage_score=data_coverage_score,
                notes=[f"任务模式 {task_mode} 不需要委员会"],
            )

        # ── 第二层：席位激活 ────────────────────────────────────
        activated: List[str] = list(rules["required"])
        optional_candidates: List[str] = list(rules["optional"])

        # 用户强制完整评估 → 激活所有可选席位
        if user_force_full:
            activated.extend(optional_candidates)
            optional_candidates = []
            notes.append("用户要求完整评估，激活全部可选席位")

        # 有未验证事件 → 自动激活 audit 席位
        if has_unverified_events and "audit" not in activated:
            if "audit" in optional_candidates:
                optional_candidates.remove("audit")
            activated.append("audit")
            notes.append("检测到未验证事件，自动激活真伪审计席位")

        # 决策模式且为 Watchlist 资产 → 激活 pnl 席位
        if task_mode == "decision" and is_watchlist_asset and "pnl" not in activated:
            if "pnl" in optional_candidates:
                optional_candidates.remove("pnl")
            activated.append("pnl")
            notes.append("正式决策 + 关注资产，激活执行经济性顾问")

        # ── 第三层：预算与数据完整度约束 ────────────────────────

        # 数据覆盖不足 → 剔除依赖量化数据的席位
        if data_coverage_score < 0.3:
            quant_seats = {"probability", "structure", "pnl"}
            before_count = len(activated)
            activated = [s for s in activated if s not in quant_seats or s in rules["required"][:1]]
            if len(activated) < before_count:
                notes.append(f"数据覆盖不足 ({data_coverage_score:.1%})，降级量化类席位")

        # 非 Watchlist 资产 + 非 decision 模式 → 限制到最多 2 席
        if not is_watchlist_asset and task_mode != "decision" and not user_force_full:
            if len(activated) > 2:
                activated = activated[:2]
                notes.append("非关注资产，限制最多 2 席")

        # 去重
        activated = list(dict.fromkeys(activated))

        # 计算 token 预算
        seat_budgets = {}
        total_seat_extra = 0
        for seat_id in activated:
            cfg = SEAT_REGISTRY.get(seat_id)
            if cfg:
                seat_budgets[seat_id] = cfg.extra_token_budget
                total_seat_extra += cfg.extra_token_budget

        shared_budget = _SHARED_BASE_TOKENS
        total_estimated = shared_budget + total_seat_extra + len(activated) * 2000  # 每席位约 2K 输出

        # 超预算 → 逐步降级可选席位
        budget_constrained = False
        while total_estimated > max_tokens and activated:
            # 找到最后一个非必选席位移除
            removable = [s for s in activated if s not in rules["required"]]
            if not removable:
                break
            removed = removable[-1]
            activated.remove(removed)
            del seat_budgets[removed]
            total_seat_extra -= SEAT_REGISTRY[removed].extra_token_budget
            total_estimated = shared_budget + total_seat_extra + len(activated) * 2000
            budget_constrained = True
            notes.append(f"Token 预算约束，移除 {SEAT_REGISTRY[removed].role_label}")

        return OrchestrationPlan(
            task_mode=task_mode,
            activated_seats=activated,
            shared_token_budget=shared_budget,
            seat_budgets=seat_budgets,
            total_estimated_tokens=total_estimated,
            needs_committee=len(activated) > 0,
            data_coverage_score=data_coverage_score,
            budget_constrained=budget_constrained,
            notes=notes,
        )

    def get_seat_config(self, seat_id: str) -> Optional[SeatConfig]:
        """获取席位配置"""
        return SEAT_REGISTRY.get(seat_id)

    def get_all_seats(self) -> Dict[str, SeatConfig]:
        """获取所有席位配置"""
        return dict(SEAT_REGISTRY)

    def format_plan_summary(self, plan: OrchestrationPlan) -> str:
        """将编排计划格式化为可读摘要"""
        lines = [
            f"任务模式: {plan.task_mode}",
            f"激活席位: {', '.join(plan.activated_seats) or '无'}",
            f"预估 Token: ~{plan.total_estimated_tokens:,}",
            f"数据覆盖: {plan.data_coverage_score:.0%}",
        ]
        if plan.budget_constrained:
            lines.append("⚠ 受 Token 预算约束，部分席位已降级")
        if plan.notes:
            lines.append("决策记录:")
            for note in plan.notes:
                lines.append(f"  · {note}")
        return "\n".join(lines)
