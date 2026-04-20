from fastapi.testclient import TestClient

from deepquery.api.app import create_app


def test_health():
    with TestClient(create_app()) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_adapters():
    with TestClient(create_app()) as client:
        r = client.get("/api/adapters")
    assert r.status_code == 200
    assert "claude_code" in r.json()["adapters"]
