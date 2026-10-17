"""speaker_profiles 表

V2 升级：从简单可信度标签升级为"研究员画像系统"。
精选社交源不是噪音池，而是"研究员池"。每个来源具备多维画像，
包括角度价值、情绪强度、逻辑稳健性、兑现记录、适用边界、时间半衰期、反向价值。

不简单地把来源分为"可信"与"不可信"：
- 有些来源角度好但逻辑不稳，适合作为启发源
- 有些来源可影响选题与反证搜索，但不应直接抬高 conviction
"""
from datetime import datetime
from sqlalchemy import String, Text, Float, JSON, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class SpeakerProfile(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "speaker_profiles"

    # ── V1 原有字段 ─────────────────────────────────────────────
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # ["别名1", "别名2"]
    affiliation: Mapped[str] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(200), nullable=True)
    domain_tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # ["宏观", "美股"]
    trust_weight: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)  # 0.0~1.0
    track_record_summary: Mapped[str] = mapped_column(Text, nullable=True)
    style_notes: Mapped[str] = mapped_column(Text, nullable=True)
    hit_rate: Mapped[float] = mapped_column(Float, nullable=True)  # 历史胜率
    total_claims: Mapped[int] = mapped_column(default=0, nullable=False)
    verified_claims: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    # ── V2：多维画像 ────────────────────────────────────────────
    perspective_value: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # 角度价值 0~1（是否经常提供别人忽略的视角）
    emotional_intensity: Mapped[float] = mapped_column(Float, default=0.3, nullable=False)
    # 情绪强度 0~1（煽动性话术或情绪放大程度，越高越需警惕）
    logic_robustness: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # 逻辑稳健性 0~1（论证链是否扎实，是否经常跳步）
    applicable_domains: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 擅长领域细分 ["宏观政策", "链上分析", "交易结构", "消息面", "情绪叙事"]
    time_half_life: Mapped[str] = mapped_column(String(20), default="short", nullable=False)
    # 观点适用时间跨度 immediate | short | medium | long
    contrarian_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # 反向指标价值 0~1（即使不准，是否具有反向指标或情绪指标意义）

    # ── V2：兑现追踪 ────────────────────────────────────────────
    hit_count: Mapped[int] = mapped_column(default=0, nullable=False)
    miss_count: Mapped[int] = mapped_column(default=0, nullable=False)
    partial_hit_count: Mapped[int] = mapped_column(default=0, nullable=False)
    avg_deviation_pct: Mapped[float] = mapped_column(Float, nullable=True)
    # 平均偏差百分比（预测方向 vs 实际方向的偏差）
    best_asset_classes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 历史表现最佳的资产类别
    worst_asset_classes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # 历史表现最差的资产类别

    # ── V2：使用建议与校准 ──────────────────────────────────────
    usage_recommendation: Mapped[str] = mapped_column(Text, nullable=True)
    # LLM 生成的简要使用建议
    # 例："适合作为宏观方向启发源，逻辑链不够稳健，不宜单独作为高置信证据"
    last_calibrated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    # 关系
    claims = relationship("ClaimCard", back_populates="speaker")

    __table_args__ = (
        Index("idx_speaker_profiles_name", "name"),
    )
