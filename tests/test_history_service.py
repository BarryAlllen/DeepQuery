"""HistoryService：会话/消息 CRUD + 级联删除 + 跨用户访问隔离。"""

from __future__ import annotations

import pytest

from deepquery.config.settings import Settings
from deepquery.history import HistoryService, SessionNotFound


@pytest.fixture
async def svc(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'h.db'}")
    s = HistoryService(settings)
    await s.create_schema()
    yield s
    await s.dispose()


async def test_ensure_session_creates_when_id_missing(svc: HistoryService):
    sess = await svc.ensure_session("alice", None, "今天天气？", adapter="claude_code")
    assert sess.user_id == "alice"
    assert sess.title == "今天天气？"
    assert sess.adapter == "claude_code"


async def test_ensure_session_reuses_existing(svc: HistoryService):
    s1 = await svc.ensure_session("alice", None, "q1", adapter=None)
    s2 = await svc.ensure_session("alice", s1.id, "q2", adapter=None)
    assert s1.id == s2.id


async def test_ensure_session_rejects_cross_user(svc: HistoryService):
    s = await svc.ensure_session("alice", None, "q", adapter=None)
    with pytest.raises(SessionNotFound):
        await svc.ensure_session("mallory", s.id, "q", adapter=None)


async def test_add_messages_and_get_session(svc: HistoryService):
    s = await svc.ensure_session("alice", None, "hi", adapter="claude_code")
    await svc.add_user_message(s.id, "hi")
    await svc.add_assistant_message(s.id, "claude_code", "hello")
    detail = await svc.get_session("alice", s.id)
    assert [m.role for m in detail.messages] == ["user", "assistant"]
    assert detail.messages[1].content == "hello"
    assert detail.messages[1].adapter == "claude_code"


async def test_stream_placeholder_finalize(svc: HistoryService):
    s = await svc.ensure_session("alice", None, "q", adapter="claude_code")
    ph = await svc.start_assistant_message(s.id, "claude_code")
    # 流中途失败：落 error_code，content 可能是半截
    await svc.finalize_assistant_message(ph.id, "half", error_code="adapter_timeout")
    detail = await svc.get_session("alice", s.id)
    assert detail.messages[0].content == "half"
    assert detail.messages[0].error_code == "adapter_timeout"


async def test_list_sessions_sorted_desc(svc: HistoryService):
    s1 = await svc.ensure_session("alice", None, "q1", None)
    s2 = await svc.ensure_session("alice", None, "q2", None)
    s3 = await svc.ensure_session("alice", None, "q3", None)
    # 刷新 s2 的 updated_at 使其排第一
    await svc.add_user_message(s2.id, "poke")
    items = await svc.list_sessions("alice")
    ids = [i.id for i in items]
    assert ids[0] == s2.id
    # 剩下两个按初始 updated_at 倒序
    assert set(ids[1:]) == {s1.id, s3.id}


async def test_list_sessions_cursor_before(svc: HistoryService):
    s1 = await svc.ensure_session("alice", None, "q1", None)
    s2 = await svc.ensure_session("alice", None, "q2", None)
    items = await svc.list_sessions("alice", limit=1)
    assert items[0].id == s2.id
    # 下一页
    next_items = await svc.list_sessions("alice", limit=10, before=items[0].updated_at)
    assert [i.id for i in next_items] == [s1.id]


async def test_delete_cascades_messages(svc: HistoryService):
    s = await svc.ensure_session("alice", None, "q", None)
    await svc.add_user_message(s.id, "q")
    await svc.add_assistant_message(s.id, "claude_code", "a")
    await svc.delete_session("alice", s.id)
    with pytest.raises(SessionNotFound):
        await svc.get_session("alice", s.id)
    # 级联后 messages 也应该不存在（直接再 get 会 404，间接验证）
    items = await svc.list_sessions("alice")
    assert items == []


async def test_delete_rejects_cross_user(svc: HistoryService):
    s = await svc.ensure_session("alice", None, "q", None)
    with pytest.raises(SessionNotFound):
        await svc.delete_session("mallory", s.id)


async def test_title_truncated_to_40(svc: HistoryService):
    long = "x" * 100
    s = await svc.ensure_session("alice", None, long, None)
    assert len(s.title) == 40
