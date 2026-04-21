"""问答历史路由：列出/查看/删除会话。

v1 单租户：所有会话都挂在 user_id='default' 下；后续 v2 接入鉴权后
只要改 _current_user 从 token 解出真实用户 id 即可，其他代码不动。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from deepquery.api.deps import get_history_service
from deepquery.history import HistoryService, SessionNotFound
from deepquery.knowledge import DEFAULT_USER

router = APIRouter()


def _current_user() -> str:
    # v2 预留钩子：未来接入 token 后改这里即可
    return DEFAULT_USER


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    before: datetime | None = None,
    svc: HistoryService = Depends(get_history_service),
) -> dict:
    items = await svc.list_sessions(_current_user(), limit=limit, before=before)
    return {"sessions": [asdict(s) for s in items]}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    svc: HistoryService = Depends(get_history_service),
) -> dict:
    try:
        detail = await svc.get_session(_current_user(), session_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="session 不存在")
    return asdict(detail)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    svc: HistoryService = Depends(get_history_service),
) -> dict:
    try:
        await svc.delete_session(_current_user(), session_id)
    except SessionNotFound:
        raise HTTPException(status_code=404, detail="session 不存在")
    return {"ok": True}
