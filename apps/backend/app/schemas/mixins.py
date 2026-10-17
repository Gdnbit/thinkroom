"""公共 Mixin：所有 ORM 模型共享的字段和行为"""
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    """created_at / updated_at 自动时间戳"""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ULIDPrimaryKey:
    """使用 ULID 作为主键（字符串格式）"""
    id: Mapped[str] = mapped_column(String(26), primary_key=True)
