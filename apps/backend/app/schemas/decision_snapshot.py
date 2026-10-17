"""decision_snapshots 表 — 决策时点证据快照

核心目的：冻结某次分析/委员会/裁决时刻系统"当时知道什么"。
这是 Decision Replay 的基础设施，确保后验分析不受后见之明污染。

每次委员会辩论或 Briefing 生成时，自动创建一条 DecisionSnapshot，
记录送入的私域观点、行情状态、事件列表、资金流信号、SignalScorer 输出、
激活的席位、最终裁决结果。

后验字段（outcome_direction, replay_notes 等）在 Decision Replay 时填入：
- 自动：LifecycleWorkflow 扫描时检查相关事件的 outcome_status 变化
- 手动：用户通过 Override API 标注
"""
from datetime import datetime
from sqlalchemy import String, Text, Float, Integer, DateTime, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class DecisionSnapshot(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "decision_snapshots"
    __table_args__ = (
        Index("uq_decision_snapshots_trigger", "trigger_type", "trigger_id", unique=True),
    )

    # ── 关联对象 ────────────────────────────────────────────────
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # debate | briefing | chat | scout
    trigger_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    asset: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)

    # ── 时点快照 ────────────────────────────────────────────────
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # ── 记忆层快照 ──────────────────────────────────────────────
    claims_snapshot: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 当时送入的私域观点：[{id, summary, stance, confidence, trust_weight, speaker_name}]
    claims_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    data_coverage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 数据覆盖摘要：{total_active, date_range, stance_distribution}

    # ── 证据层快照 ──────────────────────────────────────────────
    market_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 当时的行情状态：{price, rsi, trend, volatility, ...}
    flow_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 当时的资金流信号：{funding_rate, oi_change, taker_ratio, ...}
    events_snapshot: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 当时的事件列表：[{id, title, truth_status, importance, direction}]
    macro_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 当时的宏观数据：{fed_rate, us10y, dxy, ...}

    # ── 决策层快照 ──────────────────────────────────────────────
    signal_scorecard: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # SignalScorer 输出：{claim_score, market_score, flow_score, event_score, composite, ...}
    activated_seats: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 激活了哪些席位：["baseline", "probability", "risk", "structure"]
    evidence_budget: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # token 预算分配：{shared_tokens: 3000, baseline_extra: 500, ...}

    # ── 输出快照 ────────────────────────────────────────────────
    final_direction: Mapped[str] = mapped_column(String(20), nullable=True)
    # bullish | bearish | neutral
    final_conviction: Mapped[float] = mapped_column(Float, nullable=True)
    final_action: Mapped[str] = mapped_column(String(50), nullable=True)
    # enter | watch | avoid | hold | scale_in | reduce
    synthesis_summary: Mapped[str] = mapped_column(Text, nullable=True)

    # ── 后验（Decision Replay 时填入） ─────────────────────────
    outcome_direction: Mapped[str] = mapped_column(String(20), nullable=True)
    # 事后实际方向
    outcome_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_pnl_pct: Mapped[float] = mapped_column(Float, nullable=True)
    # 事后收益百分比（如可计算）
    replay_notes: Mapped[str] = mapped_column(Text, nullable=True)
    # 回放分析笔记（LLM 或用户填写）
    replay_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    # pending | replayed | skipped
    misleading_evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 回放时识别出的误导性证据：[{type, id, description}]
    key_missed_signals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 回放时识别出的遗漏关键信号
