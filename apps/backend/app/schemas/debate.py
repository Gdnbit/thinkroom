"""debate_records / debate_rounds / final_adjudications 表"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


ADJUDICATION_METADATA_KEY = "_adjudication_metadata"


def store_adjudication_metadata(
    action_economics: dict | None,
    *,
    probability_score: float,
    audit_score: float,
    evidence_quality_warnings: list[str] | None,
) -> dict:
    """Persist non-column adjudication fields in the compatible JSON payload."""
    payload = dict(action_economics or {})
    payload[ADJUDICATION_METADATA_KEY] = {
        "probability_score": float(probability_score),
        "audit_score": float(audit_score),
        "evidence_quality_warnings": list(evidence_quality_warnings or []),
    }
    return payload


def load_adjudication_metadata(action_economics: dict | None) -> dict:
    payload = action_economics if isinstance(action_economics, dict) else {}
    metadata = payload.get(ADJUDICATION_METADATA_KEY)
    return dict(metadata) if isinstance(metadata, dict) else {}


class DebateRecord(Base, ULIDPrimaryKey, TimestampMixin):
    """一次委员会辩论会话"""
    __tablename__ = "debate_records"

    question: Mapped[str] = mapped_column(Text, nullable=False)
    asset: Mapped[str] = mapped_column(String(50), nullable=False)
    committee_level: Mapped[str] = mapped_column(String(20), default="triple", nullable=False)
    # single | double | triple

    # 参与模型（A6/V2：新列 model_by_seat 为权威；旧三列保留向后兼容读旧数据）
    model_by_seat: Mapped[dict] = mapped_column(JSON, default=dict, nullable=True)
    # 示例：{"baseline": "committee_a", "probability": "committee_b", "risk": "committee_c",
    #        "structure": "committee_d", "audit": "committee_e", "pnl": "committee_f"}

    # ── Legacy V1 列（已 deprecated；写路径仍 dual-write 以保持向后兼容；预计 0010 版本移除）──
    model_bull: Mapped[str] = mapped_column(String(100), nullable=True)     # deprecated: 对应 baseline
    model_bear: Mapped[str] = mapped_column(String(100), nullable=True)     # deprecated: 对应 probability
    model_neutral: Mapped[str] = mapped_column(String(100), nullable=True)  # deprecated: 对应 risk

    # 状态
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    # pending | running | completed | failed

    # 上下文快照
    context_claims: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context_events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context_market: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # 最终裁决（冗余存储，方便查询）
    final_direction: Mapped[str] = mapped_column(String(20), nullable=True)
    final_conviction: Mapped[float] = mapped_column(Float, nullable=True)
    final_action: Mapped[str] = mapped_column(String(50), nullable=True)
    consensus_level: Mapped[str] = mapped_column(String(20), nullable=True)
    # strong | moderate | weak | split

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)

    # 关联生成的 briefing
    briefing_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("briefing_cards.id", ondelete="SET NULL"), nullable=True
    )

    rounds = relationship("DebateRound", back_populates="debate", order_by="DebateRound.round_number")
    adjudication = relationship("FinalAdjudication", back_populates="debate", uselist=False)


class DebateRound(Base, ULIDPrimaryKey, TimestampMixin):
    """辩论的单轮发言"""
    __tablename__ = "debate_rounds"

    debate_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("debate_records.id", ondelete="CASCADE"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3...
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # bull | bear | neutral
    model_used: Mapped[str] = mapped_column(String(100), nullable=True)

    # 发言内容
    position: Mapped[str] = mapped_column(Text, nullable=False)        # 本轮立场陈述
    key_arguments: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    challenges: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # 对其他方的质疑
    concessions: Mapped[list] = mapped_column(JSON, default=list, nullable=False) # 承认的对方观点
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=True)  # bullish|bearish|neutral

    token_count: Mapped[int] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    is_mock: Mapped[bool] = mapped_column(default=False, nullable=False)

    debate = relationship("DebateRecord", back_populates="rounds")


class FinalAdjudication(Base, ULIDPrimaryKey, TimestampMixin):
    """委员会最终裁决"""
    __tablename__ = "final_adjudications"

    debate_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("debate_records.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # 裁决结论
    direction: Mapped[str] = mapped_column(String(20), nullable=False)  # bullish|bearish|neutral
    conviction: Mapped[float] = mapped_column(Float, nullable=False)    # 0.0~1.0
    recommended_action: Mapped[str] = mapped_column(String(50), nullable=False)
    consensus_level: Mapped[str] = mapped_column(String(20), nullable=False)
    # strong_consensus | moderate_consensus | weak_consensus | split

    # 综合分析
    synthesis: Mapped[str] = mapped_column(Text, nullable=False)        # 综合叙述
    bull_score: Mapped[float] = mapped_column(Float, nullable=True)     # 多方论点得分
    bear_score: Mapped[float] = mapped_column(Float, nullable=True)     # 空方论点得分
    key_agreements: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    key_disagreements: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    minority_view: Mapped[str] = mapped_column(Text, nullable=True)     # 少数意见
    invalidation_conditions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    adjudicated_by: Mapped[str] = mapped_column(String(100), nullable=True)  # 裁决模型
    is_mock: Mapped[bool] = mapped_column(default=False, nullable=False)

    # ── V2：结构分析评分 ────────────────────────────────────────
    structure_score: Mapped[float] = mapped_column(Float, nullable=True)
    structure_warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    risk_warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    hedging_advice: Mapped[str] = mapped_column(Text, nullable=True)

    # ── V2：动作经济性评估 ──────────────────────────────────────
    action_economics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # {funding_cost_annualized, volatility_regime, waiting_value_window,
    #  hold_risk_description, action_timing, action_preference, reasoning}

    debate = relationship("DebateRecord", back_populates="adjudication")
