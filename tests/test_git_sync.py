"""git_sync 模块测试：clone / pull / status，全部基于本地 bare 仓。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from deepquery.knowledge import git_sync


def test_is_git_repo_false_for_plain_dir(tmp_path: Path):
    assert git_sync.is_git_repo(tmp_path) is False


def test_clone_then_pull_picks_up_new_commits(tmp_path: Path, bare_repo):
    dest = tmp_path / "local"
    asyncio.run(git_sync.clone(bare_repo["bare_url"], dest, branch="main"))
    assert (dest / "README.md").exists()
    assert git_sync.is_git_repo(dest)

    # 远端追加新文档
    bare_repo["push"]("notes.md", "# notes\nhello", "add notes")

    # 本地 pull 应能拉到新文件
    head = asyncio.run(git_sync.pull(dest, branch="main"))
    assert head  # short sha
    assert (dest / "notes.md").read_text() == "# notes\nhello"


def test_status_returns_repo_metadata(tmp_path: Path, bare_repo):
    dest = tmp_path / "local"
    asyncio.run(git_sync.clone(bare_repo["bare_url"], dest, branch="main"))

    st = asyncio.run(git_sync.status(dest))
    assert st.is_repo
    assert st.branch == "main"
    assert st.head
    assert st.remote_url == bare_repo["bare_url"]


def test_clone_invalid_url_raises_git_error(tmp_path: Path):
    with pytest.raises(git_sync.GitError):
        asyncio.run(
            git_sync.clone("/nonexistent/path.git", tmp_path / "x", branch="main")
        )


# ---------- 本地仓（无 remote）操作 ----------


def test_init_local_creates_repo_idempotent(tmp_path: Path):
    p = tmp_path / "local"
    asyncio.run(git_sync.init_local(p, branch="main"))
    assert git_sync.is_git_repo(p)
    # 再调一次不应报错
    asyncio.run(git_sync.init_local(p, branch="main"))
    assert git_sync.is_git_repo(p)


def test_commit_all_returns_none_when_clean(tmp_path: Path):
    p = tmp_path / "local"
    asyncio.run(git_sync.init_local(p))
    head = asyncio.run(git_sync.commit_all(p, "noop"))
    assert head is None


def test_commit_all_then_log(tmp_path: Path):
    p = tmp_path / "local"
    asyncio.run(git_sync.init_local(p))

    (p / "a.md").write_text("hello")
    h1 = asyncio.run(git_sync.commit_all(p, "add a"))
    assert h1

    (p / "a.md").write_text("hello v2")
    (p / "b.md").write_text("brand new")
    h2 = asyncio.run(git_sync.commit_all(p, "edit a + add b"))
    assert h2 and h2 != h1

    commits = asyncio.run(git_sync.log(p))
    assert len(commits) == 2
    assert commits[0].message == "edit a + add b"
    # 第二条 commit 应记录到两个文件
    assert sorted(commits[0].files) == ["a.md", "b.md"]


def test_revert_to_restores_old_content(tmp_path: Path):
    p = tmp_path / "local"
    asyncio.run(git_sync.init_local(p))
    (p / "x.md").write_text("v1")
    h1 = asyncio.run(git_sync.commit_all(p, "v1"))
    (p / "x.md").write_text("v2")
    asyncio.run(git_sync.commit_all(p, "v2"))

    asyncio.run(git_sync.revert_to(p, h1))
    assert (p / "x.md").read_text() == "v1"


def test_status_local_repo_has_no_remote(tmp_path: Path):
    p = tmp_path / "local"
    asyncio.run(git_sync.init_local(p))
    (p / "a.md").write_text("hi")
    asyncio.run(git_sync.commit_all(p, "init"))

    st = asyncio.run(git_sync.status(p))
    assert st.is_repo
    assert st.has_remote is False
    assert st.remote_url == ""
    assert st.branch == "main"
    assert st.dirty is False


def test_status_dirty_when_uncommitted_changes(tmp_path: Path):
    p = tmp_path / "local"
    asyncio.run(git_sync.init_local(p))
    (p / "a.md").write_text("v1")
    asyncio.run(git_sync.commit_all(p, "v1"))
    (p / "a.md").write_text("v2 uncommitted")

    st = asyncio.run(git_sync.status(p))
    assert st.dirty is True
