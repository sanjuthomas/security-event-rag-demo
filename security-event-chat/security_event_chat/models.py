from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)


class SourceHit(BaseModel):
    event_id: str | None = None
    instruction_id: str | None = None
    score: float
    sources: list[str]
    summary: str
    merged: dict[str, Any] | None = None
    security_event: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceHit]
    cypher: str | None = None
    graph_rows: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_ms: float | None = None
    generation_ms: float | None = None
