"""历史服务：对外提供会话/消息的 CRUD 与编排。

设计取舍：
- 把 repository 的 CRUD 内联到 service 里，v1 规模下二层抽象无收益，
  真有复杂查询再拆。
- 所有方法都接一个 user_id，跨用户访问统一 404（不泄露 session 是否存在）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deepquery.config.settings import Settings
from deepquery.history.db import make_engine, make_sessionmaker
from deepquery.history.models import Base, Message, MessageRole, Session, _utcnow

logger = logging.getLogger(__name__)


class SessionNotFound(Exception):
    """目标 session 不存在或不属于当前 user。"""


@dataclass(frozen=True)
class SessionSummary:
    id: str
    title: str
    adapter: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


@dataclass(frozen=True)
class MessageView:
    id: str
    role: str
    content: str
    adapter: str | None
    error_code: str | None
    created_at: datetime


@dataclass(frozen=True)
class SessionDetail:
    id: str
    title: str
    adapter: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageView]


class HistoryService:
    def __init__(
        self,
        settings: Settings,
        engine: AsyncEngine | None = None,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.settings = settings
        # 允许测试注入已有的 engine/sessionmaker，避免重复创建
        self._engine = engine or make_engine(settings)
        self._sessionmaker = sessionmaker or make_sessionmaker(self._engine)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def create_schema(self) -> None:
        """开发/测试环境兜底：直接 create_all。

        生产走 alembic upgrade head（见 alembic/env.py），此方法幂等。
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self._engine.dispose()

    # --- 会话/消息编排 ---

    async def ensure_session(
        self,
        user_id: str,
        session_id: str | None,
        question: str,
        adapter: str | None,
    ) -> Session:
        """拿到或新建 session；跨用户访问一律 SessionNotFound。"""
        async with self._sessionmaker() as db:
            if session_id:
                sess = await db.get(Session, session_id)
                if not sess or sess.user_id != user_id:
                    raise SessionNotFound(session_id)
                return sess
            sess = Session(
                user_id=user_id,
                title=_make_title(question),
                adapter=adapter,
            )
            db.add(sess)
            await db.commit()
            await db.refresh(sess)
            return sess

    async def add_user_message(self, session_id: str, content: str) -> Message:
        async with self._sessionmaker() as db:
            msg = Message(session_id=session_id, role=MessageRole.user.value, content=content)
            db.add(msg)
            await self._touch_session(db, session_id)
            await db.commit()
            await db.refresh(msg)
            return msg

    async def has_completed_turn(self, session_id: str) -> bool:
        """判断会话里是否已有"成功"的 assistant 轮次。

        用途：适配器决定首轮用 --session-id 建会话，之后用 --resume 续接。
        error_code 非空的 assistant 行不算成功，避免用损坏的 claude cache 续接。
        """
        from sqlalchemy import func as sqfunc

        async with self._sessionmaker() as db:
            stmt = select(sqfunc.count(Message.id)).where(
                Message.session_id == session_id,
                Message.role == MessageRole.assistant.value,
                Message.error_code.is_(None),
                Message.content != "",
            )
            return (await db.execute(stmt)).scalar_one() > 0

    async def last_successful_adapter(self, session_id: str) -> str | None:
        """返回本会话最近一条成功 assistant 消息所用的适配器名；全没有成功则返回 None。

        用途：QueryService 用来判断本轮是"同 CLI 续聊"还是"换 CLI 续聊"。
        """
        async with self._sessionmaker() as db:
            stmt = (
                select(Message.adapter)
                .where(
                    Message.session_id == session_id,
                    Message.role == MessageRole.assistant.value,
                    Message.error_code.is_(None),
                    Message.content != "",
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def messages_for_replay(self, session_id: str) -> list[MessageView]:
        """返回本会话按时间升序的所有消息；供换 CLI 时拼历史文本使用。

        失败的 assistant 行 (error_code 非空 / content 为空) 会被过滤，
        避免把"上一轮报错"作为上下文误导模型。
        """
        async with self._sessionmaker() as db:
            stmt = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.asc())
            )
            rows = (await db.execute(stmt)).scalars().all()
            out: list[MessageView] = []
            for m in rows:
                if m.role == MessageRole.assistant.value and (m.error_code or not m.content):
                    continue
                out.append(
                    MessageView(
                        id=m.id,
                        role=m.role,
                        content=m.content,
                        adapter=m.adapter,
                        error_code=m.error_code,
                        created_at=m.created_at,
                    )
                )
            return out

    async def start_assistant_message(self, session_id: str, adapter: str) -> Message:
        """流式场景：先落占位行，流结束后 finalize 更新 content。"""
        async with self._sessionmaker() as db:
            msg = Message(
                session_id=session_id,
                role=MessageRole.assistant.value,
                content="",
                adapter=adapter,
            )
            db.add(msg)
            await db.commit()
            await db.refresh(msg)
            return msg

    async def finalize_assistant_message(
        self,
        message_id: str,
        content: str,
        error_code: str | None = None,
    ) -> None:
        async with self._sessionmaker() as db:
            msg = await db.get(Message, message_id)
            if not msg:
                # 落库失败不影响主流程，只记日志
                logger.warning("assistant message %s 不存在，跳过 finalize", message_id)
                return
            msg.content = content
            msg.error_code = error_code
            await self._touch_session(db, msg.session_id)
            await db.commit()

    async def add_assistant_message(
        self,
        session_id: str,
        adapter: str,
        content: str,
        error_code: str | None = None,
    ) -> Message:
        """非流式场景：一次性落库。"""
        async with self._sessionmaker() as db:
            msg = Message(
                session_id=session_id,
                role=MessageRole.assistant.value,
                content=content,
                adapter=adapter,
                error_code=error_code,
            )
            db.add(msg)
            await self._touch_session(db, session_id)
            await db.commit()
            await db.refresh(msg)
            return msg

    # --- 查询 ---

    async def list_sessions(
        self,
        user_id: str,
        limit: int = 20,
        before: datetime | None = None,
    ) -> list[SessionSummary]:
        # 按 updated_at 倒序，cursor (before) 为上一页最后一条的 updated_at
        stmt = select(Session).where(Session.user_id == user_id)
        if before is not None:
            stmt = stmt.where(Session.updated_at < before)
        stmt = stmt.order_by(Session.updated_at.desc()).limit(limit)
        async with self._sessionmaker() as db:
            rows = (await db.execute(stmt)).scalars().all()
            summaries: list[SessionSummary] = []
            for s in rows:
                count = await _count_messages(db, s.id)
                summaries.append(
                    SessionSummary(
                        id=s.id,
                        title=s.title,
                        adapter=s.adapter,
                        created_at=s.created_at,
                        updated_at=s.updated_at,
                        message_count=count,
                    )
                )
            return summaries

    async def get_session(self, user_id: str, session_id: str) -> SessionDetail:
        async with self._sessionmaker() as db:
            sess = await db.get(Session, session_id)
            if not sess or sess.user_id != user_id:
                raise SessionNotFound(session_id)
            stmt = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.asc())
            )
            msgs = (await db.execute(stmt)).scalars().all()
            return SessionDetail(
                id=sess.id,
                title=sess.title,
                adapter=sess.adapter,
                created_at=sess.created_at,
                updated_at=sess.updated_at,
                messages=[
                    MessageView(
                        id=m.id,
                        role=m.role,
                        content=m.content,
                        adapter=m.adapter,
                        error_code=m.error_code,
                        created_at=m.created_at,
                    )
                    for m in msgs
                ],
            )

    async def delete_session(self, user_id: str, session_id: str) -> None:
        async with self._sessionmaker() as db:
            sess = await db.get(Session, session_id)
            if not sess or sess.user_id != user_id:
                raise SessionNotFound(session_id)
            await db.execute(delete(Session).where(Session.id == session_id))
            await db.commit()

    # --- 内部 ---

    async def _touch_session(self, db: AsyncSession, session_id: str) -> None:
        # SQLAlchemy onupdate=func.now() 只在 ORM 对象被修改时触发；
        # 这里显式加载并设置 updated_at，保证无字段变动也能刷新。
        sess = await db.get(Session, session_id)
        if sess:
            sess.updated_at = _utcnow()


def _make_title(question: str) -> str:
    q = question.strip().replace("\n", " ")
    return q[:40]


async def _count_messages(db: AsyncSession, session_id: str) -> int:
    from sqlalchemy import func as sqfunc

    result = await db.execute(
        select(sqfunc.count(Message.id)).where(Message.session_id == session_id)
    )
    return int(result.scalar_one())


# 公开给 API 层的类型别名
__all__: list[str] = [
    "HistoryService",
    "SessionNotFound",
    "SessionSummary",
    "SessionDetail",
    "MessageView",
]
