"""共享 fixture：用本地裸仓 + 工作树构造一个可 clone/pull 的"远端"。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_history_db(tmp_path: Path, monkeypatch):
    """所有测试用独立 tmp 目录里的 SQLite，避免污染仓库根目录/互相串味。

    注意：get_settings 用了 lru_cache，所以要先 env，再清缓存。
    """
    db = tmp_path / "deepquery_test.db"
    monkeypatch.setenv("DEEPQUERY_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from deepquery.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def bare_repo(tmp_path: Path) -> dict:
    """创建一个 bare 仓 + 工作树并写入若干 MD，返回 {bare_url, workdir, push}.

    通过 push 函数往里加文件再提交，模拟"远端有更新"。
    """
    bare = tmp_path / "remote.git"
    work = tmp_path / "remote_work"
    work.mkdir()

    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "test@local")
    _git(work, "config", "user.name", "test")
    (work / "README.md").write_text("# initial\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")

    def push(filename: str, content: str, message: str) -> None:
        (work / filename).write_text(content)
        _git(work, "add", "-A")
        _git(work, "commit", "-m", message)
        _git(work, "push", "origin", "main")

    yield {"bare_url": str(bare), "workdir": work, "push": push}

    # 清理（tmp_path 一般会清，但 git 进程偶尔会留 lock）
    for p in (bare, work):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
