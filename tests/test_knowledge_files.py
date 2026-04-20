"""HTTP 文件写入/删除接口的端到端测试。

覆盖：写新文件 / 覆盖文件 / 删除 / 路径越界 / 大小限制 / 乐观锁冲突 /
不存在的仓 / 写后 commit_all 真的产生了 commit。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from deepquery.api.app import create_app
from deepquery.config.settings import KnowledgeRepo, get_settings


@contextmanager
def _client(tmp_path: Path, **extra):
    get_settings.cache_clear()
    s = get_settings()
    s.knowledge_dir = tmp_path / "kb"
    s.knowledge_repos = [KnowledgeRepo(name="local", subdir="local")]
    for k, v in extra.items():
        setattr(s, k, v)
    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        get_settings.cache_clear()


def _write(c, **body):
    body.setdefault("message", "auto")
    return c.post("/api/knowledge/local/files", json=body)


def test_write_creates_file_and_commits(tmp_path: Path):
    with _client(tmp_path) as c:
        r = _write(c, path="hello.md", content="hi", message="add hello")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["head"]
        assert (tmp_path / "kb" / "local" / "hello.md").read_text() == "hi"

        # log 应有这条
        log = c.get("/api/knowledge/local/log").json()["commits"]
        assert log[0]["message"] == "add hello"
        assert "hello.md" in log[0]["files"]


def test_write_overwrites_existing_file(tmp_path: Path):
    with _client(tmp_path) as c:
        _write(c, path="a.md", content="v1", message="init")
        r = _write(c, path="a.md", content="v2", message="update")
        assert r.status_code == 200
        assert (tmp_path / "kb" / "local" / "a.md").read_text() == "v2"


def test_write_same_content_returns_no_changes(tmp_path: Path):
    with _client(tmp_path) as c:
        _write(c, path="a.md", content="same", message="init")
        r = _write(c, path="a.md", content="same", message="noop")
        assert r.status_code == 200
        assert r.json()["no_changes"] is True


def test_write_creates_nested_dirs(tmp_path: Path):
    with _client(tmp_path) as c:
        r = _write(c, path="orders/sub/timeout.md", content="x", message="nested")
        assert r.status_code == 200
        assert (tmp_path / "kb" / "local" / "orders" / "sub" / "timeout.md").exists()


def test_path_traversal_is_rejected(tmp_path: Path):
    with _client(tmp_path) as c:
        for bad in ["../escape.md", "../../etc/passwd", "/tmp/abs.md", "", "."]:
            r = _write(c, path=bad, content="x")
            assert r.status_code in (400, 422), f"path={bad!r} should be rejected, got {r.status_code}"


def test_size_limit_enforced(tmp_path: Path):
    with _client(tmp_path, knowledge_max_file_bytes=10) as c:
        r = _write(c, path="big.md", content="x" * 50)
        assert r.status_code == 413


def test_unknown_repo_returns_404(tmp_path: Path):
    with _client(tmp_path) as c:
        r = c.post(
            "/api/knowledge/no-such/files",
            json={"path": "a.md", "content": "x", "message": "m"},
        )
    assert r.status_code == 404


def test_optimistic_lock_conflict(tmp_path: Path):
    with _client(tmp_path) as c:
        # 第 1 次写，记录 head
        r1 = _write(c, path="a.md", content="v1", message="init")
        head1 = r1.json()["head"]

        # 别人偷偷又改了一次 → head 推进
        _write(c, path="a.md", content="v2", message="rival")

        # 当前调用方仍以为基于 head1，应该 409
        r = _write(
            c,
            path="a.md",
            content="my edit",
            message="my edit",
            base_commit_sha=head1,
        )
        assert r.status_code == 409
        assert "current_head" in r.json()["detail"]


def test_optimistic_lock_passes_with_current_head(tmp_path: Path):
    with _client(tmp_path) as c:
        r1 = _write(c, path="a.md", content="v1", message="init")
        head1 = r1.json()["head"]
        r = _write(
            c, path="a.md", content="v2", message="ok", base_commit_sha=head1
        )
        assert r.status_code == 200


def test_delete_removes_file_and_commits(tmp_path: Path):
    with _client(tmp_path) as c:
        _write(c, path="x.md", content="x", message="add")
        r = c.request(
            "DELETE",
            "/api/knowledge/local/files",
            json={"path": "x.md", "message": "rm"},
        )
        assert r.status_code == 200
        assert not (tmp_path / "kb" / "local" / "x.md").exists()
        log = c.get("/api/knowledge/local/log").json()["commits"]
        assert log[0]["message"] == "rm"


def test_delete_nonexistent_returns_404(tmp_path: Path):
    with _client(tmp_path) as c:
        r = c.request(
            "DELETE",
            "/api/knowledge/local/files",
            json={"path": "ghost.md", "message": "rm"},
        )
        assert r.status_code == 404
