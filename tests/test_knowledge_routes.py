"""knowledge 路由集成测试：通过 FastAPI 端到端验证 status / sync / webhook。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from deepquery.api.app import create_app
from deepquery.config.settings import KnowledgeRepo, get_settings


@contextmanager
def _client(tmp_path: Path, repos: list[KnowledgeRepo], **extra):
    # 重置 lru_cache 上的 settings，使本测试用 monkey-patched 配置
    get_settings.cache_clear()
    s = get_settings()
    s.knowledge_dir = tmp_path / "kb"
    s.knowledge_repos = repos
    for k, v in extra.items():
        setattr(s, k, v)
    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        get_settings.cache_clear()


def test_status_endpoint_reports_local_repo(tmp_path: Path):
    with _client(tmp_path, [KnowledgeRepo(name="local", subdir="local")]) as c:
        r = c.get("/api/knowledge/status")
    assert r.status_code == 200
    body = r.json()
    assert len(body["repos"]) == 1
    assert body["repos"][0]["name"] == "local"
    # 本地仓启动时会被 git init，但没有 remote
    assert body["repos"][0]["is_repo"] is True
    assert body["repos"][0]["has_remote"] is False


def test_sync_all_endpoint_pulls_git_repo(tmp_path: Path, bare_repo):
    repos = [KnowledgeRepo(name="shared", url=bare_repo["bare_url"], subdir="shared")]
    with _client(tmp_path, repos) as c:
        # 先 push 一个新文件
        bare_repo["push"]("hello.md", "world", "add hello")
        r = c.post("/api/knowledge/sync")
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["ok"] is True
    assert (tmp_path / "kb" / "shared" / "hello.md").read_text() == "world"


def test_webhook_requires_secret_when_configured(tmp_path: Path, bare_repo):
    repos = [KnowledgeRepo(name="shared", url=bare_repo["bare_url"], subdir="shared")]
    with _client(tmp_path, repos, knowledge_webhook_secret="s3cret") as c:
        # 缺 header → 401
        r = c.post("/api/knowledge/webhook")
        assert r.status_code == 401

        # 错的 → 401
        r = c.post("/api/knowledge/webhook", headers={"X-Deepquery-Secret": "wrong"})
        assert r.status_code == 401

        # 对的 → 200
        r = c.post("/api/knowledge/webhook", headers={"X-Deepquery-Secret": "s3cret"})
        assert r.status_code == 200


def test_webhook_skips_check_when_secret_empty(tmp_path: Path):
    with _client(tmp_path, [KnowledgeRepo(name="local", subdir="local")]) as c:
        r = c.post("/api/knowledge/webhook")
    # 本地仓 sync 是 no-op，但应该 200
    assert r.status_code == 200


def test_local_repo_commit_log_revert_flow(tmp_path: Path):
    """端到端验证本地 git 工作流：写文件 → commit → log → 改 → commit → revert。"""
    repos = [KnowledgeRepo(name="local", subdir="local")]
    with _client(tmp_path, repos) as c:
        kb = tmp_path / "kb" / "local"

        # 1. 写第一份文档并 commit
        (kb / "a.md").write_text("v1")
        r = c.post("/api/knowledge/local/commit", json={"message": "add a"})
        assert r.status_code == 200, r.text
        first_head = r.json()["head"]
        assert first_head and not r.json()["no_changes"]

        # 2. 改文档再 commit
        (kb / "a.md").write_text("v2")
        r = c.post("/api/knowledge/local/commit", json={"message": "update a"})
        assert r.status_code == 200
        second_head = r.json()["head"]
        assert second_head != first_head

        # 3. log 应该有两条
        r = c.get("/api/knowledge/local/log")
        assert r.status_code == 200
        commits = r.json()["commits"]
        assert len(commits) == 2
        # 最新在前
        assert commits[0]["message"] == "update a"
        assert commits[1]["message"] == "add a"

        # 4. 工作区干净时 commit 应返回 no_changes=True
        r = c.post("/api/knowledge/local/commit", json={"message": "noop"})
        assert r.status_code == 200
        assert r.json()["no_changes"] is True

        # 5. revert 到第一次 commit，文件内容应回到 v1
        r = c.post(
            "/api/knowledge/local/revert", json={"commit": commits[1]["sha"]}
        )
        assert r.status_code == 200
        assert (kb / "a.md").read_text() == "v1"


def test_log_unknown_repo_returns_404(tmp_path: Path):
    with _client(tmp_path, [KnowledgeRepo(name="local", subdir="local")]) as c:
        r = c.get("/api/knowledge/no-such/log")
    assert r.status_code == 404
