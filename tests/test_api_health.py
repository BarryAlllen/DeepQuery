from fastapi.testclient import TestClient

from deepquery.api.app import create_app


def test_health():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_adapters():
    client = TestClient(create_app())
    r = client.get("/api/adapters")
    assert r.status_code == 200
    assert "claude_code" in r.json()["adapters"]
