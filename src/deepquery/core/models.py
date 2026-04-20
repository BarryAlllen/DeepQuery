from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class Query(BaseModel):
    question: str
    adapter: str | None = None  # override default
    session_id: str | None = None
    context: dict[str, str] = Field(default_factory=dict)


class Answer(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str | None = None
    adapter: str
    question: str
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
