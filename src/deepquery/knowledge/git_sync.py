"""异步 Git 子进程包装。

只用 `git` 命令本身（不引入 GitPython 等重量依赖），
满足知识库同步所需的最少能力：
  - 远程仓：clone / pull / status
  - 本地仓：init / commit / log / show / revert
所有调用都是异步的，便于在 FastAPI 后台任务里并发触发。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class GitError(RuntimeError):
    """git 子命令非零退出。"""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(cmd[1:])} -> {returncode}: {stderr.strip()}")
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class GitStatus:
    is_repo: bool
    head: str = ""           # 当前 HEAD 的短 sha
    branch: str = ""         # 当前分支
    remote_url: str = ""     # origin 的 url；本地仓为空
    has_remote: bool = False
    dirty: bool = False      # 工作区是否有未提交改动


@dataclass
class CommitInfo:
    sha: str
    short_sha: str
    author: str
    date: str
    message: str
    files: list[str] = field(default_factory=list)


async def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 120) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise GitError(cmd, -1, f"timeout after {timeout}s")
    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")
    if proc.returncode != 0:
        raise GitError(cmd, proc.returncode or -1, stderr)
    return stdout


def is_git_repo(path: Path) -> bool:
    # 同步函数即可：仅 stat 检查，不开 subprocess
    return (path / ".git").exists()


# ---------- 远程仓操作 ----------

async def clone(url: str, dest: Path, *, branch: str = "main", depth: int = 1) -> None:
    """浅克隆远端到 dest（必须是不存在的目录或空目录）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", str(depth), "--branch", branch, url, str(dest)]
    logger.info("git clone %s -> %s", url, dest)
    await _run(cmd, timeout=300)


async def pull(path: Path, *, branch: str = "main") -> str:
    """在已存在的 git 仓库上 fetch+reset 到远端 branch；返回新的 HEAD 短 sha。

    用 fetch+reset 而不是 pull，避免本地有意外提交时的 merge 冲突——
    我们把"远端"视为唯一事实源，本地只是一个工作副本。
    """
    await _run(["git", "fetch", "--depth", "1", "origin", branch], cwd=path, timeout=120)
    await _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=path, timeout=60)
    return (await _run(["git", "rev-parse", "--short", "HEAD"], cwd=path)).strip()


# ---------- 通用查询 ----------

async def _has_remote(path: Path) -> bool:
    try:
        await _run(["git", "remote", "get-url", "origin"], cwd=path)
        return True
    except GitError:
        return False


async def _has_commits(path: Path) -> bool:
    try:
        await _run(["git", "rev-parse", "HEAD"], cwd=path)
        return True
    except GitError:
        return False


async def status(path: Path) -> GitStatus:
    if not is_git_repo(path):
        return GitStatus(is_repo=False)
    has_remote = await _has_remote(path)
    remote = ""
    if has_remote:
        remote = (await _run(["git", "remote", "get-url", "origin"], cwd=path)).strip()
    if not await _has_commits(path):
        # 仓库刚 init，还没任何 commit
        return GitStatus(is_repo=True, has_remote=has_remote, remote_url=remote)
    head = (await _run(["git", "rev-parse", "--short", "HEAD"], cwd=path)).strip()
    branch = (
        await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    ).strip()
    porcelain = (await _run(["git", "status", "--porcelain"], cwd=path)).strip()
    return GitStatus(
        is_repo=True,
        head=head,
        branch=branch,
        remote_url=remote,
        has_remote=has_remote,
        dirty=bool(porcelain),
    )


# ---------- 本地仓操作 ----------

async def init_local(
    path: Path, *, branch: str = "main", author_name: str = "DeepQuery", author_email: str = "deepquery@local"
) -> None:
    """在 path 上初始化本地 git 仓（无 remote），并设置默认 author。

    幂等：若已是 git 仓则只确保 author 配置；否则 init + 默认配置。
    """
    path.mkdir(parents=True, exist_ok=True)
    if not is_git_repo(path):
        await _run(["git", "init", "-b", branch], cwd=path)
        logger.info("git init local repo at %s", path)
    # 仓库级 author 配置（不污染全局）
    await _run(["git", "config", "user.name", author_name], cwd=path)
    await _run(["git", "config", "user.email", author_email], cwd=path)


async def commit_all(
    path: Path,
    message: str,
    *,
    author_name: str | None = None,
    author_email: str | None = None,
) -> str | None:
    """把工作区所有改动 add + commit；无改动时返回 None。返回新 commit 的短 sha。"""
    # 检查是否真有变更，否则 git commit 会报 "nothing to commit"
    porcelain = (await _run(["git", "status", "--porcelain"], cwd=path)).strip()
    if not porcelain:
        return None

    cmd_commit = ["git", "commit", "-m", message]
    if author_name and author_email:
        cmd_commit = [
            "git",
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-m",
            message,
        ]

    await _run(["git", "add", "-A"], cwd=path)
    await _run(cmd_commit, cwd=path)
    return (await _run(["git", "rev-parse", "--short", "HEAD"], cwd=path)).strip()


async def log(path: Path, *, limit: int = 50) -> list[CommitInfo]:
    """返回最近 N 条 commit；空仓返回 []。"""
    if not await _has_commits(path):
        return []
    # 使用 unit separator 0x1f / record separator 0x1e，避免和 message 内容冲突
    fmt = "%H%x1f%h%x1f%an <%ae>%x1f%ad%x1f%s"
    out = await _run(
        [
            "git",
            "log",
            f"-n{limit}",
            "--date=iso-strict",
            f"--pretty=format:{fmt}",
            "--name-only",
        ],
        cwd=path,
    )
    commits: list[CommitInfo] = []
    # 输出形如：
    #   <fmt 行>
    #   file1
    #   file2
    #   <空行>
    #   <下一条 fmt 行>
    blocks = [b for b in out.split("\n\n") if b.strip()]
    for block in blocks:
        lines = block.split("\n")
        head = lines[0]
        files = [ln for ln in lines[1:] if ln.strip()]
        sha, short, author, date, msg = head.split("\x1f", 4)
        commits.append(
            CommitInfo(sha=sha, short_sha=short, author=author, date=date, message=msg, files=files)
        )
    return commits


async def revert_to(path: Path, commit_sha: str) -> str:
    """硬回滚工作树到指定 commit；返回新 HEAD 短 sha。

    这会丢弃 commit 之后的所有改动 - 调用方应在 UI 上明确提示用户。
    """
    await _run(["git", "reset", "--hard", commit_sha], cwd=path)
    return (await _run(["git", "rev-parse", "--short", "HEAD"], cwd=path)).strip()

