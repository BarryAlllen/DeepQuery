"""HTTP /api/sessions 路由：列表/详情/删除。

复杂的 seed 路径放到 HistoryService 的单元测试里；这里只覆盖 HTTP 层。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from deepquery.api.app import create_app


def test_list_empty_initially():
    with TestClient(create_app()) as c:
        r = c.get("/api/sessions")
    assert r.status_code == 200
    assert r.json()["sessions"] == []


def test_get_not_found():
    with TestClient(create_app()) as c:
        r = c.get("/api/sessions/doesnotexist")
    assert r.status_code == 404


def test_delete_not_found():
    with TestClient(create_app()) as c:
        r = c.delete("/api/sessions/doesnotexist")
    assert r.status_code == 404


def test_limit_param_validation():
    with TestClient(create_app()) as c:
        r = c.get("/api/sessions?limit=0")
    assert r.status_code == 422
