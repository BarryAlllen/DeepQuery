"""SQLAlchemy ORM 模型：会话 + 消息。

只记录用户可见的 Q/A 对，不存中间工具调用/思考过程。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid_hex() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    # Python 侧生成，保留微秒分辨率；SQLite CURRENT_TIMESTAMP 只有秒级
    # 会让"按时间倒序 + cursor 分页"失真。用 timezone-aware UTC 避免 3.12 弃用告警。
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    # v1 恒为 'default'；v2 接入真实用户系统后落真实 id
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 会话标题：首条 question 截断 40 字，后续可 PATCH
    title: Mapped[str] = mapped_column(String(200), default="")
    # 创建时默认 adapter（单条消息还可在 Message.adapter 覆盖）
    adapter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    __table_args__ = (Index("ix_sessions_user_updated", "user_id", "updated_at"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid_hex)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    # user / assistant
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    # 仅 assistant 有值：记录本条由哪个 CLI 产出
    adapter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # assistant 失败时落错误码，content 可能为空或半截流式输出
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, nullable=False, index=True
    )

    session: Mapped[Session] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)
