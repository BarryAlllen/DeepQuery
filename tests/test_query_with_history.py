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


def test_resume_flag_switches_after_first_success_turn():
    """首轮 options 里 resume=False，续轮应为 True。

    通过 Mock adapter 的类级别 options 捕获来断言。
    """
    captured: list[dict] = []

    class _CapturingAdapter(BaseCLIAdapter):
        name = "mock_capture"
        supports_native_session = True  # 让同 CLI 续轮走 resume 分支

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.append(dict(self.options))

        def build_command(self, prompt: str):  # pragma: no cover
            return ["true"]

        async def run(self, prompt: str):
            yield "ok"

    registry._REGISTRY["mock_capture"] = _CapturingAdapter

    with TestClient(create_app()) as c:
        r1 = c.post("/api/query", json={"question": "q1", "adapter": "mock_capture"})
        sid = r1.json()["session_id"]
        # 第一轮：虽然请求没带 session_id，但服务端新建了一个并注入；resume=False
        assert captured[0]["resume"] is False
        assert captured[0]["session_id"] == sid

        c.post(
            "/api/query",
            json={"question": "q2", "adapter": "mock_capture", "session_id": sid},
        )
        # 第二轮：已经有成功的 assistant 轮次，应 resume=True 并带上 session_id
        assert captured[1]["resume"] is True
        assert captured[1]["session_id"] == sid


def _make_capture_adapter(
    name: str,
    prompts: list[str],
    options: list[dict],
    *,
    supports_native_session: bool = False,
):
    """工厂：注册一个按名字记录 prompt/options 的 mock 适配器。"""

    class _A(BaseCLIAdapter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            options.append(dict(self.options))

        def build_command(self, prompt: str):  # pragma: no cover
            return ["true"]

        async def run(self, prompt: str):
            prompts.append(prompt)
            yield f"[{name}] 收到"

    _A.name = name
    _A.supports_native_session = supports_native_session
    registry._REGISTRY[name] = _A


def test_switch_cli_injects_replay_prefix_and_drops_session_id():
    """场景：claude 聊一轮后换到另一个 CLI，该 CLI 应收到"前置历史 + 当前问题"
    的拼接 prompt，且不携带 CLI 原生 session_id（因为另一家 CLI 不认识上一家的 id）。"""
    a_prompts: list[str] = []
    a_options: list[dict] = []
    b_prompts: list[str] = []
    b_options: list[dict] = []
    # A 支持原生 session（模拟 claude），B 不支持（模拟 opencode）
    _make_capture_adapter("mock_a", a_prompts, a_options, supports_native_session=True)
    _make_capture_adapter("mock_b", b_prompts, b_options, supports_native_session=False)

    with TestClient(create_app()) as c:
        r1 = c.post("/api/query", json={"question": "我叫吴赛", "adapter": "mock_a"})
        sid = r1.json()["session_id"]

        r2 = c.post(
            "/api/query",
            json={"question": "我叫什么？", "adapter": "mock_b", "session_id": sid},
        )
        assert r2.status_code == 200

    # 首家 CLI 收到的是裸问题，不含历史前缀
    assert a_prompts == ["我叫吴赛"]
    # 换到 mock_b 后：
    # 1) 它必须收到前置的历史文本 + 当前问题
    assert len(b_prompts) == 1
    assert "我叫吴赛" in b_prompts[0]
    assert "我叫什么？" in b_prompts[0]
    assert b_prompts[0].endswith("我叫什么？")
    # 2) CLI 原生 session_id 不应再注入给 mock_b（它不认 mock_a 的 session）
    assert b_options[0].get("session_id") is None
    assert b_options[0].get("resume", False) is False


def test_same_cli_native_session_followup_uses_resume_without_replay():
    """同 CLI 且适配器声明支持原生 session：走 --resume，prompt 不加历史前缀。"""
    prompts: list[str] = []
    options: list[dict] = []
    _make_capture_adapter("mock_same_native", prompts, options, supports_native_session=True)

    with TestClient(create_app()) as c:
        r1 = c.post("/api/query", json={"question": "q1", "adapter": "mock_same_native"})
        sid = r1.json()["session_id"]
        c.post(
            "/api/query",
            json={"question": "q2", "adapter": "mock_same_native", "session_id": sid},
        )

    assert prompts == ["q1", "q2"]
    # 第二轮走 resume，仍带 session_id
    assert options[1]["session_id"] == sid
    assert options[1]["resume"] is True


def test_same_cli_without_native_session_uses_replay_prefix():
    """同 CLI 但适配器不支持原生 session（如 opencode）：
    续轮走 replay 前缀，不传 session_id/resume。"""
    prompts: list[str] = []
    options: list[dict] = []
    _make_capture_adapter("mock_same_replay", prompts, options, supports_native_session=False)

    with TestClient(create_app()) as c:
        r1 = c.post("/api/query", json={"question": "q1", "adapter": "mock_same_replay"})
        sid = r1.json()["session_id"]
        c.post(
            "/api/query",
            json={"question": "q2", "adapter": "mock_same_replay", "session_id": sid},
        )

    # 首轮没有历史前缀；续轮必须包含 q1（来自 replay）
    assert prompts[0] == "q1"
    assert "q1" in prompts[1] and prompts[1].endswith("q2")
    assert options[1].get("session_id") is None
    assert options[1].get("resume", False) is False
