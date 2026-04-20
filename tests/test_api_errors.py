"""API 层错误处理：未知 adapter 必须返回结构化 400。"""

from fastapi.testclient import TestClient

from deepquery.api.app import create_app


def test_unknown_adapter_returns_structured_error():
    client = TestClient(create_app())
    r = client.post("/api/query", json={"question": "hi", "adapter": "no-such-tool"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "unknown_adapter"
    assert body["error"]["adapter"] == "no-such-tool"
    assert "no-such-tool" in body["error"]["message"]
