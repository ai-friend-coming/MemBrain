"""Pydantic response schemas for the dataset-viewer API."""

from datetime import datetime

from pydantic import BaseModel


class DatasetOut(BaseModel):
    id: int
    name: str
    task_count: int


class TaskOut(BaseModel):
    id: int
    task_id: str
    session_count: int
    qa_count: int


class QAPairOut(BaseModel):
    id: int
    question_id: str
    question: str
    answer: str
    category: str | None
    evidence: list[str]
    options: dict[str, str] | None
    reasoning: str | None


class MessageOut(BaseModel):
    id: int
    position: int
    dia_id: str | None
    speaker: str
    content: str
    message_time: datetime | None
    message_time_raw: str | None


class SessionOut(BaseModel):
    id: int
    session_number: int
    session_time: datetime | None
    session_time_raw: str | None
    messages: list[MessageOut]


class TaskDetailOut(BaseModel):
    id: int
    task_id: str
    sessions: list[SessionOut]
    qa_pairs: list[QAPairOut]


class MemoryTimeAnnotationOut(BaseModel):
    id: int
    time_raw: str
    time_resolved: str


class MemoryFactRefOut(BaseModel):
    fact_id: int
    entity_id: str
    alias_text: str


class FactPageItemOut(BaseModel):
    id: int
    text: str
    batch_index: int | None = None
    fact_ts: str | None = None
    status: str | None = None


class MemoryEntityOut(BaseModel):
    id: int
    entity_id: str
    canonical_ref: str
    desc: str
    aliases: list[str] = []
    orphan_facts: list[FactPageItemOut] = []


class MemoryTreeNodeOut(BaseModel):
    id: int
    entity_id: str
    parent_id: int | None
    node_type: str
    fact_id: int | None
    description: str | None
    fact_text: str | None = None
    fact_batch_index: int | None = None
    fact_ts: str | None = None
    fact_status: str | None = None


class MemoryGraphOut(BaseModel):
    entities: list[MemoryEntityOut]
    fact_refs: list[MemoryFactRefOut]
    tree_nodes: list[MemoryTreeNodeOut]


class FactsPageOut(BaseModel):
    total: int
    offset: int
    limit: int
    batch_options: list[int] = []
    facts: list[FactPageItemOut]


class SessionSummaryPageItemOut(BaseModel):
    session_number: int
    subject: str
    content: str


class SessionSummariesPageOut(BaseModel):
    total: int
    offset: int
    limit: int
    summaries: list[SessionSummaryPageItemOut]


class RunTagInfo(BaseModel):
    run_tag: str
