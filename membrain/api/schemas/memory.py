"""Request / response schemas for the memory REST API.

Endpoints:
  POST /api/memory         — store / digest / store+digest a conversation session
  POST /api/memory/search  — search memory for a question
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ── Shared ──────────────────────────────────────────────────────────────────


class MessageIn(BaseModel):
    speaker: str
    content: str
    message_time: str = ""


# ── POST /api/memory ────────────────────────────────────────────────────────


class MemoryRequest(BaseModel):
    dataset: str
    task: str
    messages: list[MessageIn] = []
    session_time: str = ""
    store: bool = True
    digest: bool = True
    agent_profile: str | None = None


class MemoryResponse(BaseModel):
    dataset_id: int
    task_pk: int
    session_id: int | None = None
    session_number: int | None = None
    digested_sessions: int = 0
    status: str


# ── POST /api/memory/search ───────────────────────────────────────────────


class MemorySearchRequest(BaseModel):
    dataset: str
    task: str
    question: str
    mode: Literal["direct", "expand", "reflect"] = "expand"
    strategy: Literal["rrf", "rerank"] = "rrf"
    top_k: int | None = None


class RetrievedFactOut(BaseModel):
    fact_id: int
    text: str
    source: str
    rerank_score: float = 0.0
    time_info: str = ""
    entity_ref: str = ""
    aspect_path: str = ""


class RetrievedSessionOut(BaseModel):
    session_summary_id: int
    session_id: int
    subject: str
    content: str
    score: float
    source: str
    contributing_facts: int = 0


class RetrievedMessageOut(BaseModel):
    message_id: int
    session_id: int
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
