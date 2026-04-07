"""Shared data transfer objects for the retrieval domain.

These types are the public interface of the retrieval pipeline:
- input side: consumed by budget_pack and retrieval strategies
- output side: produced by infra/retrieval/* query functions

Defined here (in core) so that retrieval/application and infra/retrieval
can both import from a neutral location without either depending on the other.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievedFact:
    fact_id: int
    text: str
    source: str  # "bm25" | "embed" | "tree"
    rerank_score: float = 0.0
    time_info: str = (
        ""  # resolved date, e.g. "2023-05-07" or "yesterday [2023-05-07, 2023-05-07]"
    )
    entity_ref: str = ""
    aspect_path: str = ""
    aspect_summary: str = ""
    session_number: int | None = None


@dataclass
class RetrievedMessage:
    message_id: int
    session_id: int
    speaker: str
    content: str
    message_time: str  # ISO-formatted datetime string
    bm25_score: float = 0.0
    query: str = ""  # which refined_query matched this


@dataclass
class RetrievedSession:
    session_summary_id: int
    session_id: int
    subject: str
    content: str
    score: float
    source: str  # "bm25" | "fact_agg" | "conjunctive"
    contributing_facts: int = 0
    session_number: int | None = None


@dataclass
class AspectInfo:
    """Tree-structure context for a single fact."""

    entity_ref: str  # canonical name, e.g. "Caroline"
    entity_desc: str  # entity short description, e.g. "user's mother"
    leaf_desc: str  # leaf aspect description (direct parent of fact)
    mid_desc: str  # mid-level aspect description (empty for 2-level trees)
    path: str  # e.g. "Career > Work history" or "Career"
    leaf_key: str  # dedup key: "entity::leaf_desc"
    mid_key: str  # dedup key: "entity::mid_desc" or "entity::leaf_desc"
