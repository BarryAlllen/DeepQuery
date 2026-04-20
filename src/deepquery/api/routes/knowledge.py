"""知识库管理路由：手动 / webhook 触发同步、查询状态、本地仓 commit/log/revert/files。"""

from __future__ import annotations

import hmac
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from deepquery.api.deps import get_knowledge_service
from deepquery.knowledge import FileConflictError, KnowledgeFileError, KnowledgeService

router = APIRouter()


@router.get("/knowledge/status")
async def status(svc: KnowledgeService = Depends(get_knowledge_service)) -> dict:
    repos = await svc.status()
    return {"repos": [asdict(r) for r in repos]}


@router.post("/knowledge/sync")
async def sync(
    name: str | None = None,
    svc: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    """手动触发同步。带 `name` 参数则只同步该仓，不带则同步全部。

    本地仓（无 remote）调用此接口是 no-op，会返回 ok。
    """
    if name:
        result = await svc.sync_one(name)
        return {"results": [asdict(result)]}
    results = await svc.sync_all()
    return {"results": [asdict(r) for r in results]}


@router.post("/knowledge/webhook")
async def webhook(
    request: Request,
    x_deepquery_secret: str | None = Header(default=None),
    svc: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    """供 GitHub/GitLab/Gitea push 事件回调。

    简化处理：不解析具体 payload（不同平台格式不同），命中即触发全量 sync。
    通过共享密钥（DEEPQUERY_KNOWLEDGE_WEBHOOK_SECRET）做最小校验。
    """
    expected = request.app.state.settings.knowledge_webhook_secret
    if expected:
        if not x_deepquery_secret or not hmac.compare_digest(expected, x_deepquery_secret):
            raise HTTPException(status_code=401, detail="invalid webhook secret")
    results = await svc.sync_all()
    return {"results": [asdict(r) for r in results]}


# ---------- 本地仓：commit / log / revert ----------


class CommitRequest(BaseModel):
    # 必填，commit message
    message: str = Field(..., min_length=1)
    # 可选，指定提交者；不传则用仓库默认 author
    author_name: str | None = None
    author_email: str | None = None


@router.post("/knowledge/{name}/commit")
async def commit(
    name: str,
    body: CommitRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    """把指定知识库的工作区改动 commit。Web 编辑入口将来走这里。"""
    result = await svc.commit_changes(
        name,
        body.message,
        author_name=body.author_name,
        author_email=body.author_email,
    )
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.get("/knowledge/{name}/log")
async def log(
    name: str,
    limit: int = 50,
    svc: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    try:
        commits = await svc.log(name, limit=limit)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"commits": [asdict(c) for c in commits]}


class RevertRequest(BaseModel):
    commit: str = Field(..., min_length=4, description="目标 commit 的完整或短 sha")


@router.post("/knowledge/{name}/revert")
async def revert(
    name: str,
    body: RevertRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    """硬回滚到指定 commit。该 commit 之后的改动会被丢弃，调用方需在 UI 上确认。"""
    result = await svc.revert_to(name, body.commit)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


# ---------- 文件写入接口（HTTP 上传 / 编辑入口）----------


class WriteFileRequest(BaseModel):
    # 仓内相对路径，如 "orders/timeout.md"
    path: str = Field(..., min_length=1)
    # MD 文本内容（UTF-8）
    content: str
    # commit message
    message: str = Field(..., min_length=1)
    # 可选乐观锁：基于哪个 head 修改；不传则不校验
    base_commit_sha: str | None = None
    author_name: str | None = None
    author_email: str | None = None


class DeleteFileRequest(BaseModel):
    path: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    base_commit_sha: str | None = None
    author_name: str | None = None
    author_email: str | None = None


def _raise_file_error(e: KnowledgeFileError) -> None:
    detail: dict = {"message": e.message}
    if isinstance(e, FileConflictError) and e.current_head:
        detail["current_head"] = e.current_head
    raise HTTPException(status_code=e.http_status, detail=detail)


@router.post("/knowledge/{name}/files")
async def write_file(
    name: str,
    body: WriteFileRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    """新增 / 覆盖单个文件。写入成功后会自动 commit，无需再调 commit 接口。"""
    try:
        result = await svc.write_file(
            name,
            body.path,
            body.content,
            body.message,
            base_commit_sha=body.base_commit_sha,
            author_name=body.author_name,
            author_email=body.author_email,
        )
    except KnowledgeFileError as e:
        _raise_file_error(e)
    return {"path": body.path, **asdict(result)}


@router.delete("/knowledge/{name}/files")
async def delete_file(
    name: str,
    body: DeleteFileRequest,
    svc: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    """删除单个文件并自动 commit。"""
    try:
        result = await svc.delete_file(
            name,
            body.path,
            body.message,
            base_commit_sha=body.base_commit_sha,
            author_name=body.author_name,
            author_email=body.author_email,
        )
    except KnowledgeFileError as e:
        _raise_file_error(e)
    return {"path": body.path, **asdict(result)}
