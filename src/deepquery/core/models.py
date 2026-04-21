from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field


class Query(BaseModel):
    question: str
    adapter: str | None = None  # override default
    session_id: str | None = None
    # 用户身份。v1 由 API 层默认填 'default'，v2 接入鉴权后从 token 解出真实用户
    user_id: str | None = None
    context: dict[str, str] = Field(default_factory=dict)


class Answer(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str | None = None
    adapter: str
    question: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
