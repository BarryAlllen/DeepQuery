"""知识库统一服务：初始化、同步、查询某个用户可见的目录。

设计要点：
- 多仓挂载：每个 KnowledgeRepo 落到 knowledge_dir/<subdir>。
- 远程仓：启动时 clone，后续 fetch+reset 同步。
- 本地仓（无 url）：启动时 git init，提供 commit/log/revert 接口供 Web 编辑等入口使用。
- 用户隔离钩子：`dirs_for_user(user_id)` 现在按 scope 过滤——
  v1 user_id 永远是 'default'，仅返回 public/未限定的仓；
  v2 加用户系统后只需在这一个方法里实现 team/private 的真实过滤。
- 同步用单仓互斥锁，防止并发 pull 互相踩。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from deepquery.config.settings import KnowledgeRepo, Settings
from deepquery.knowledge import git_sync

logger = logging.getLogger(__name__)

DEFAULT_USER = "default"


@dataclass
class RepoStatus:
    name: str
    subdir: str
    path: str
    scope: str
    is_repo: bool
    has_remote: bool = False
    head: str = ""
    branch: str = ""
    remote_url: str = ""
    dirty: bool = False
    last_synced_at: float = 0.0
    last_error: str = ""


@dataclass
class SyncResult:
    name: str
    ok: bool
    head: str = ""
    error: str = ""


@dataclass
class CommitResult:
    name: str
    ok: bool
    head: str = ""           # 新 commit 短 sha；无变更时为空
    no_changes: bool = False  # True 表示工作区干净，没东西可 commit
    error: str = ""


class KnowledgeFileError(Exception):
    """文件写入相关的业务错误。`http_status` 让路由层直接映射状态码。"""

    def __init__(self, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


class FileConflictError(KnowledgeFileError):
    """乐观锁失败：文件已被他人修改。"""

    def __init__(self, message: str, *, current_head: str = "") -> None:
        super().__init__(message, http_status=409)
        self.current_head = current_head


@dataclass
class _RepoState:
    repo: KnowledgeRepo
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_synced_at: float = 0.0
    last_error: str = ""


class KnowledgeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._states: dict[str, _RepoState] = {
            r.name: _RepoState(repo=r) for r in settings.knowledge_repos
        }

    # ---------- 路径解析 ----------

    def repo_path(self, repo: KnowledgeRepo) -> Path:
        return (self.settings.knowledge_dir / repo.subdir).resolve()

    def dirs_for_user(self, user_id: str = DEFAULT_USER) -> list[Path]:
        """返回某用户可见的本地目录列表。v1 仅按 scope 过滤。"""
        out: list[Path] = []
        for st in self._states.values():
            r = st.repo
            if r.scope == "public":
                visible = True
            elif r.scope == "team":
                # v1 没有用户/团队系统：team 仓默认对所有人可见，等 v2 改这里
                visible = True
            elif r.scope == "private":
                # v1 没有用户系统：private 仓默认仅 'default' 用户可见
                visible = user_id == DEFAULT_USER and (not r.user_ids or DEFAULT_USER in r.user_ids)
            else:
                visible = False
            if visible:
                p = self.repo_path(r)
                if p.exists():
                    out.append(p)
        return out

    # ---------- 初始化与同步 ----------

    async def initialize(self) -> None:
        """启动时 ensure 每个配置的仓库本地存在。

        - 有 url 的：不存在则 clone
        - 无 url（本地仓）：不存在则 mkdir，且若不是 git 仓则 git init，
          以便后续支持版本历史 / 回滚 / Web 编辑提交
        """
        self.settings.knowledge_dir.mkdir(parents=True, exist_ok=True)
        for st in self._states.values():
            path = self.repo_path(st.repo)
            try:
                if st.repo.url:
                    if path.exists() and any(path.iterdir()):
                        if not git_sync.is_git_repo(path):
                            logger.warning(
                                "目录 %s 已存在但不是 git 仓库，跳过 clone（请人工处理）", path
                            )
                        continue
                    await git_sync.clone(st.repo.url, path, branch=st.repo.branch)
                else:
                    # 本地仓：先建目录，再 git init（幂等，已有 .git 不会重置）
                    await git_sync.init_local(path, branch=st.repo.branch)
            except git_sync.GitError as e:
                st.last_error = str(e)
                logger.error("初始化仓 %s 失败: %s", st.repo.name, e)

    async def sync_one(self, name: str) -> SyncResult:
        st = self._states.get(name)
        if not st:
            return SyncResult(name=name, ok=False, error="unknown repo")
        if not st.repo.url:
            # 本地仓无远程，sync 就是 no-op；返回 ok 让前端少做特判
            return SyncResult(name=name, ok=True)
        async with st.lock:
            path = self.repo_path(st.repo)
            try:
                if not git_sync.is_git_repo(path):
                    await git_sync.clone(st.repo.url, path, branch=st.repo.branch)
                head = await git_sync.pull(path, branch=st.repo.branch)
                st.last_synced_at = time.time()
                st.last_error = ""
                return SyncResult(name=name, ok=True, head=head)
            except git_sync.GitError as e:
                st.last_error = str(e)
                logger.warning("sync %s 失败: %s", name, e)
                return SyncResult(name=name, ok=False, error=str(e))

    async def sync_all(self) -> list[SyncResult]:
        # 并发 pull 多个仓，整体更快；每个仓内部仍互斥
        return await asyncio.gather(*(self.sync_one(name) for name in self._states))

    async def status(self) -> list[RepoStatus]:
        out: list[RepoStatus] = []
        for st in self._states.values():
            path = self.repo_path(st.repo)
            git_st = (
                await git_sync.status(path) if path.exists() else git_sync.GitStatus(False)
            )
            out.append(
                RepoStatus(
                    name=st.repo.name,
                    subdir=st.repo.subdir,
                    path=str(path),
                    scope=st.repo.scope,
                    is_repo=git_st.is_repo,
                    has_remote=git_st.has_remote,
                    head=git_st.head,
                    branch=git_st.branch,
                    remote_url=git_st.remote_url,
                    dirty=git_st.dirty,
                    last_synced_at=st.last_synced_at,
                    last_error=st.last_error,
                )
            )
        return out

    # ---------- 本地仓：commit / log / revert ----------

    def _require_local_repo(self, name: str) -> _RepoState:
        st = self._states.get(name)
        if not st:
            raise KeyError(f"未知的知识库仓: {name}")
        return st

    async def commit_changes(
        self,
        name: str,
        message: str,
        *,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> CommitResult:
        """把指定仓工作区的所有改动提交。无改动时返回 no_changes=True。"""
        try:
            st = self._require_local_repo(name)
        except KeyError as e:
            return CommitResult(name=name, ok=False, error=str(e))
        path = self.repo_path(st.repo)
        if not git_sync.is_git_repo(path):
            return CommitResult(name=name, ok=False, error="目录不是 git 仓，无法 commit")
        async with st.lock:
            try:
                head = await git_sync.commit_all(
                    path, message, author_name=author_name, author_email=author_email
                )
                if head is None:
                    return CommitResult(name=name, ok=True, no_changes=True)
                return CommitResult(name=name, ok=True, head=head)
            except git_sync.GitError as e:
                return CommitResult(name=name, ok=False, error=str(e))

    async def log(self, name: str, *, limit: int = 50) -> list[git_sync.CommitInfo]:
        st = self._require_local_repo(name)
        path = self.repo_path(st.repo)
        if not git_sync.is_git_repo(path):
            return []
        return await git_sync.log(path, limit=limit)

    async def revert_to(self, name: str, commit_sha: str) -> CommitResult:
        try:
            st = self._require_local_repo(name)
        except KeyError as e:
            return CommitResult(name=name, ok=False, error=str(e))
        path = self.repo_path(st.repo)
        if not git_sync.is_git_repo(path):
            return CommitResult(name=name, ok=False, error="目录不是 git 仓，无法 revert")
        async with st.lock:
            try:
                head = await git_sync.revert_to(path, commit_sha)
                return CommitResult(name=name, ok=True, head=head)
            except git_sync.GitError as e:
                return CommitResult(name=name, ok=False, error=str(e))

    # ---------- HTTP 写入入口（写文件 + 自动 commit）----------

    def _safe_resolve(self, repo: KnowledgeRepo, rel_path: str) -> Path:
        """把相对路径解析成绝对路径，并强制限定在仓内。

        防御 `../`、绝对路径、符号链接逃逸；非法路径抛 KnowledgeFileError(400)。
        """
        raw = (rel_path or "").strip()
        if not raw:
            raise KnowledgeFileError("path 不能为空")
        # 必须先判断绝对路径，再做 lstrip 等清洗
        if raw.startswith("/") or Path(raw).is_absolute():
            raise KnowledgeFileError("path 必须是相对路径")
        rel = raw.lstrip("/")
        if not rel or rel in (".", ".."):
            raise KnowledgeFileError("path 不能为空")
        root = self.repo_path(repo)
        target = (root / rel).resolve()
        # 必须仍在 root 之下；用 relative_to 比拼字符串更稳
        try:
            target.relative_to(root)
        except ValueError as e:
            raise KnowledgeFileError("path 越界，禁止访问仓外文件") from e
        return target

    async def write_file(
        self,
        name: str,
        rel_path: str,
        content: str,
        message: str,
        *,
        base_commit_sha: str | None = None,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> CommitResult:
        """写入/创建文件并立刻 commit。失败时不留下脏工作区。"""
        try:
            st = self._require_local_repo(name)
        except KeyError as e:
            raise KnowledgeFileError(str(e), http_status=404) from e

        # 大小限制（按 utf-8 字节算，前端按字符算可能差异，以服务端为准）
        max_bytes = getattr(self.settings, "knowledge_max_file_bytes", 0)
        if max_bytes and len(content.encode("utf-8")) > max_bytes:
            raise KnowledgeFileError(
                f"文件超过单文件大小上限 {max_bytes} 字节", http_status=413
            )

        path = self.repo_path(st.repo)
        if not git_sync.is_git_repo(path):
            raise KnowledgeFileError("目录不是 git 仓，无法写入", http_status=400)

        target = self._safe_resolve(st.repo, rel_path)

        async with st.lock:
            # 乐观锁：调用方声称基于某 head 修改，期间仓 head 不能变
            if base_commit_sha:
                git_st = await git_sync.status(path)
                # 接受短/长 sha 互相比对
                cur = git_st.head or ""
                if not (cur and (cur == base_commit_sha or base_commit_sha.startswith(cur))):
                    raise FileConflictError(
                        f"base_commit_sha={base_commit_sha} 与当前 head={cur} 不一致，请刷新后重试",
                        current_head=cur,
                    )

            # 写入
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

            # commit；若没有实际改动（覆盖了相同内容）也按成功返回，no_changes=True
            try:
                head = await git_sync.commit_all(
                    path, message, author_name=author_name, author_email=author_email
                )
            except git_sync.GitError as e:
                # commit 失败时尝试回滚工作区，避免留下未提交的脏改动
                try:
                    await git_sync._run(["git", "checkout", "--", str(target)], cwd=path)
                except git_sync.GitError:
                    pass
                raise KnowledgeFileError(f"commit 失败: {e}", http_status=500) from e

            if head is None:
                return CommitResult(name=name, ok=True, no_changes=True)
            return CommitResult(name=name, ok=True, head=head)

    async def delete_file(
        self,
        name: str,
        rel_path: str,
        message: str,
        *,
        base_commit_sha: str | None = None,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> CommitResult:
        try:
            st = self._require_local_repo(name)
        except KeyError as e:
            raise KnowledgeFileError(str(e), http_status=404) from e

        path = self.repo_path(st.repo)
        if not git_sync.is_git_repo(path):
            raise KnowledgeFileError("目录不是 git 仓，无法删除", http_status=400)
        target = self._safe_resolve(st.repo, rel_path)
        if not target.exists():
            raise KnowledgeFileError(f"文件不存在: {rel_path}", http_status=404)
        if target.is_dir():
            raise KnowledgeFileError("不允许删除目录", http_status=400)

        async with st.lock:
            if base_commit_sha:
                git_st = await git_sync.status(path)
                cur = git_st.head or ""
                if not (cur and (cur == base_commit_sha or base_commit_sha.startswith(cur))):
                    raise FileConflictError(
                        f"base_commit_sha={base_commit_sha} 与当前 head={cur} 不一致",
                        current_head=cur,
                    )
            target.unlink()
            try:
                head = await git_sync.commit_all(
                    path, message, author_name=author_name, author_email=author_email
                )
            except git_sync.GitError as e:
                raise KnowledgeFileError(f"commit 失败: {e}", http_status=500) from e
            if head is None:
                return CommitResult(name=name, ok=True, no_changes=True)
            return CommitResult(name=name, ok=True, head=head)

    # ---------- 后台自动同步 ----------

    async def run_auto_pull(self) -> None:
        """常驻后台任务：按 knowledge_auto_pull_seconds 周期性同步全部仓库。"""
        interval = self.settings.knowledge_auto_pull_seconds
        if interval <= 0:
            return
        logger.info("auto-pull 已启用，间隔 %ss", interval)
        while True:
            try:
                await asyncio.sleep(interval)
                await self.sync_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 后台任务必须吞掉异常，否则会悄悄退出
                logger.exception("auto-pull 异常")

