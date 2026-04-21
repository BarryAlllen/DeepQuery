"""QueryService 与历史的集成：一次 /api/query 后 session_id 可续聊，历史能查到 Q/A 对。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from deepquery.api.app import create_app
from deepquery.cli_adapters import registry
from deepquery.cli_adapters.base import BaseCLIAdapter


class _MockAdapter(BaseCLIAdapter):
    name = "mock_echo"

    def build_command(self, prompt: str):  # pragma: no cover - 未用
        return ["true"]

    async def run(self, prompt: str):
        # 覆盖 run：绕过子进程，直接 yield 两段模拟输出
        yield "MOCK:"
        yield prompt


def _ensure_registered():
    registry._REGISTRY["mock_echo"] = _MockAdapter


def test_query_writes_history_and_roundtrips_session():
    _ensure_registered()
    with TestClient(create_app()) as c:
        # 1. 首次问：session_id 不传，服务应分配一个
        r1 = c.post("/api/query", json={"question": "你好", "adapter": "mock_echo"})
        assert r1.status_code == 200
        body1 = r1.json()
        sid = body1["session_id"]
        assert sid
        assert body1["content"] == "MOCK:你好"

        # 2. 续聊：带上 sid
        r2 = c.post(
            "/api/query", json={"question": "再来一个", "adapter": "mock_echo", "session_id": sid}
        )
        assert r2.status_code == 200
        assert r2.json()["session_id"] == sid

        # 3. 会话详情应有 4 条消息：2 user + 2 assistant
        r3 = c.get(f"/api/sessions/{sid}")
        assert r3.status_code == 200
        msgs = r3.json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
        assert msgs[0]["content"] == "你好"
        assert msgs[1]["content"] == "MOCK:你好"

        # 4. 列表里能看到
        r4 = c.get("/api/sessions")
        items = r4.json()["sessions"]
        assert any(i["id"] == sid for i in items)


def test_query_with_unknown_session_returns_404():
    _ensure_registered()
    with TestClient(create_app()) as c:
        r = c.post(
            "/api/query",
            json={"question": "?", "adapter": "mock_echo", "session_id": "nope"},
        )
    assert r.status_code == 404
