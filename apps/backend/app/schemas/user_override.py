"""user_overrides 表 — 人工修正审计留痕

因为本项目是"决策辅助系统"，不是自动执行系统，必须保留人工修正能力。
所有人工修正都留痕，以便后续校准系统。

支持的修正类型：
- correction: 纠正错误（如事件真假状态、来源可信度）
- annotation: 补充标注（如添加使用建议）
- adoption_feedback: 用户对系统建议的采纳/不采纳反馈
- learning_mark: 标记为"值得学习"或"误判案例"
"""
from sqlalchemy import String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class UserOverride(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "user_overrides"

    # ── 修正目标 ────────────────────────────────────────────────
    target_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # speaker | event | claim | adjudication | briefing | decision_snapshot
    target_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)

    # ── 修正内容 ────────────────────────────────────────────────
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 被修正的字段名，如 "truth_status", "trust_weight", "direction"
    old_value: Mapped[str] = mapped_column(Text, nullable=True)
    # 修正前的值（JSON 序列化）
    new_value: Mapped[str] = mapped_column(Text, nullable=True)
    # 修正后的值（JSON 序列化）

    # ── 修正元数据 ──────────────────────────────────────────────
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    # 修正原因
    override_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # correction | annotation | adoption_feedback | learning_mark
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # 附加数据，如 adoption_feedback 时存储 {adopted: true/false, user_action: "..."}
