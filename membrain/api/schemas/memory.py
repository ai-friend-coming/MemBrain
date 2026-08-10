"""Request / response schemas for the memory REST API.

Endpoints:
  POST /api/memory         — store / digest / store+digest a conversation session
  POST /api/memory/search  — search memory for a question
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from membrain.api.schemas.common import TraceOut

# ── Shared ──────────────────────────────────────────────────────────────────


class MessageIn(BaseModel):
    speaker: str
    content: str
    message_time: str = ""


# ── POST /api/memory ────────────────────────────────────────────────────────


class MemoryRequest(BaseModel):
    """接收带外部聊天来源的记忆写入参数。"""

    dataset: str
    task: str
    chat_id: str = Field(min_length=1, max_length=255)
    messages: list[MessageIn] = []
    session_time: str = ""
    store: bool = True
    digest: bool = True
    wait_for_digest: bool = False
    agent_profile: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=255)


class MemoryResponse(BaseModel):
    dataset_id: int
    task_pk: int
    session_id: int | None = None
    session_number: int | None = None
    digested_sessions: int = 0
    status: str
    trace: TraceOut
    request_id: str | None = None


class MemoryJobResponse(BaseModel):
    """返回持久化异步 digest 任务的最终事实。"""

    request_id: str
    dataset_id: int
    task_pk: int
    session_id: int
    session_number: int
    status: Literal["queued", "running", "succeeded", "failed"]
    digested_sessions: int
    trace: TraceOut
    error: str = ""


# ── POST /api/memory/search ───────────────────────────────────────────────


class MemorySearchRequest(BaseModel):
    dataset: str
    task: str
    question: str
    mode: Literal["direct", "expand", "reflect"] = "expand"
    strategy: Literal["rrf", "rerank"] = "rrf"
    top_k: int | None = None


class RetrievedFactOut(BaseModel):
    """返回事实内容及其全部外部聊天来源。"""

    fact_id: int
    text: str
    source_chat_ids: list[str]
    source: str
    rerank_score: float = 0.0
    time_info: str = ""
    entity_ref: str = ""
    aspect_path: str = ""


class RetrievedSessionOut(BaseModel):
    """返回会话摘要及其唯一外部聊天来源。"""

    session_summary_id: int
    session_id: int
    chat_id: str
    subject: str
    content: str
    score: float
    source: str
    contributing_facts: int = 0


class RetrievedMessageOut(BaseModel):
    """返回原始消息及其唯一外部聊天来源。"""

    message_id: int
    session_id: int
    chat_id: str
    speaker: str
    content: str
    message_time: str
    bm25_score: float = 0.0


class MemorySearchResponse(BaseModel):
    packed_context: str
    packed_token_count: int
    fact_ids: list[int]
    facts: list[RetrievedFactOut]
    sessions: list[RetrievedSessionOut]
    raw_messages: list[RetrievedMessageOut] = []
    trace: TraceOut
