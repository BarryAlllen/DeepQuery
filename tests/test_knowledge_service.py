"""KnowledgeService 行为测试：初始化（clone）、增量同步、按用户解析目录。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from deepquery.config.settings import KnowledgeRepo, Settings
from deepquery.knowledge import KnowledgeService


def _settings(tmp_path: Path, repos: list[KnowledgeRepo]) -> Settings:
    return Settings(
        env="testing",
        knowledge_dir=tmp_path / "kb",
        knowledge_repos=repos,
    )


def test_initialize_clones_when_url_given(tmp_path: Path, bare_repo):
    s = _settings(
        tmp_path,
        [KnowledgeRepo(name="shared", url=bare_repo["bare_url"], subdir="shared")],
    )
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())

    cloned = tmp_path / "kb" / "shared"
    assert (cloned / "README.md").exists()


def test_initialize_creates_dir_when_no_url(tmp_path: Path):
    s = _settings(tmp_path, [KnowledgeRepo(name="local", subdir="local")])
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())
    assert (tmp_path / "kb" / "local").is_dir()


def test_sync_one_pulls_new_commits(tmp_path: Path, bare_repo):
    s = _settings(
        tmp_path,
        [KnowledgeRepo(name="shared", url=bare_repo["bare_url"], subdir="shared")],
    )
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())

    bare_repo["push"]("doc.md", "fresh", "add doc")

    result = asyncio.run(svc.sync_one("shared"))
    assert result.ok
    assert result.head
    assert (tmp_path / "kb" / "shared" / "doc.md").read_text() == "fresh"


def test_sync_one_unknown_returns_error(tmp_path: Path):
    svc = KnowledgeService(_settings(tmp_path, [KnowledgeRepo(name="x", subdir="x")]))
    result = asyncio.run(svc.sync_one("not-exist"))
    assert not result.ok


def test_dirs_for_user_filters_by_scope(tmp_path: Path):
    repos = [
        KnowledgeRepo(name="public", subdir="public", scope="public"),
        KnowledgeRepo(name="team", subdir="team", scope="team"),
        KnowledgeRepo(name="private", subdir="private", scope="private"),
    ]
    s = _settings(tmp_path, repos)
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())

    dirs = svc.dirs_for_user("default")
    names = sorted(p.name for p in dirs)
    # v1：public/team 默认所有人可见，private 仅 default 用户可见
    assert names == ["private", "public", "team"]


def test_status_reports_repo_state(tmp_path: Path, bare_repo):
    s = _settings(
        tmp_path,
        [KnowledgeRepo(name="shared", url=bare_repo["bare_url"], subdir="shared")],
    )
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())

    statuses = asyncio.run(svc.status())
    assert len(statuses) == 1
    st = statuses[0]
    assert st.name == "shared"
    assert st.is_repo
    assert st.branch == "main"
    assert st.head


# ---------- 本地仓（无 url）特有行为 ----------


def test_initialize_local_repo_does_git_init(tmp_path: Path):
    s = _settings(tmp_path, [KnowledgeRepo(name="local", subdir="local")])
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())
    # 本地仓应被 git init
    p = tmp_path / "kb" / "local"
    assert (p / ".git").exists()


def test_commit_log_revert_local_repo(tmp_path: Path):
    s = _settings(tmp_path, [KnowledgeRepo(name="local", subdir="local")])
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())
    p = tmp_path / "kb" / "local"

    # 第一次 commit
    (p / "doc.md").write_text("v1")
    r1 = asyncio.run(svc.commit_changes("local", "add doc"))
    assert r1.ok and r1.head and not r1.no_changes

    # 第二次 commit
    (p / "doc.md").write_text("v2")
    r2 = asyncio.run(svc.commit_changes("local", "update"))
    assert r2.ok and r2.head != r1.head

    # log
    commits = asyncio.run(svc.log("local"))
    assert [c.message for c in commits] == ["update", "add doc"]

    # 工作区干净 → no_changes
    r3 = asyncio.run(svc.commit_changes("local", "noop"))
    assert r3.ok and r3.no_changes

    # revert
    rv = asyncio.run(svc.revert_to("local", commits[1].sha))
    assert rv.ok
    assert (p / "doc.md").read_text() == "v1"


def test_commit_unknown_repo_returns_error(tmp_path: Path):
    s = _settings(tmp_path, [KnowledgeRepo(name="local", subdir="local")])
    svc = KnowledgeService(s)
    asyncio.run(svc.initialize())
    r = asyncio.run(svc.commit_changes("nope", "x"))
    assert not r.ok
    assert "未知" in r.error or "unknown" in r.error.lower()
