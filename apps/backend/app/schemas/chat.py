"""chat_sessions / chat_messages 表 — 知识库问答会话持久化"""
from datetime import datetime
from sqlalchemy import String, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class ChatSession(Base, ULIDPrimaryKey, TimestampMixin):
    """一个对话 session"""
    __tablename__ = "chat_sessions"

    title: Mapped[str] = mapped_column(String(200), default="新对话", nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="briefing", nullable=False)
    # briefing | debate
    asset: Mapped[str] = mapped_column(String(20), default="GENERAL", nullable=False)

    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session",
        order_by="ChatMessage.created_at", cascade="all, delete-orphan"
    )


class ChatMessage(Base, ULIDPrimaryKey, TimestampMixin):
    """一条消息（user 问题或 assistant 响应）"""
    __tablename__ = "chat_messages"

    session_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)     # user: question text; assistant: executive_summary
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # assistant payload: full briefing JSON or debate result

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
