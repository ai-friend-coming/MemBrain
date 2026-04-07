"""Thread-safe local memory search runner — bypasses the HTTP API layer.

A single ``LocalSearchRunner`` instance is shared across all QA worker threads.
The underlying SQLAlchemy engine uses a connection pool (default pool_size=20)
so concurrent threads each borrow their own connection without contention.

Strategy selects the ranking module:
  ``"rrf"``    — RRF score fusion (default)
  ``"rerank"`` — Cross-encoder reranking

Usage::

    runner = LocalSearchRunner(strategy="rrf")
    try:
        result = runner.search(task_pk=42, question="Where did Alice go?")
        context = result["packed_context"]
    finally:
        runner.cleanup()
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx
from sqlalchemy import create_engine
from sqlalchemy import text as sa_text
from sqlalchemy.orm import sessionmaker

import membrain.retrieval.application.retrieval as _retrieval
from membrain.config import settings
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.clients.query_rewrite import rewrite_query
from membrain.infra.retrieval.fact_retrieval import (
    bm25_search_messages,
    bm25_search_sessions,
)

log = logging.getLogger(__name__)

_VALID_STRATEGIES = {"rrf", "rerank"}


class LocalSearchRunner:
    """In-process memory search, shared across QA worker threads."""

    def __init__(
        self,
        strategy: Literal["rrf", "rerank"] = "rrf",
        pool_size: int | None = None,
    ) -> None:
        if strategy not in _VALID_STRATEGIES:
            raise ValueError(
                f"Unknown strategy: {strategy!r}. Choose from {list(_VALID_STRATEGIES)}"
            )
        self._strategy = strategy

        sz = pool_size or settings.QA_SEARCH_POOL_SIZE
        self._engine = create_engine(
            settings.DATABASE_URL,
            pool_pre_ping=True,
            pool_size=sz,
            max_overflow=10,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            pool_recycle=settings.DB_POOL_RECYCLE,
        )
        self._sf = sessionmaker(bind=self._engine)
        self._embed_client = EmbeddingClient()
        self._http_client = httpx.Client(timeout=60.0)

    def search(
        self,
        *,
        task_pk: int,
        question: str,
        run_tag: str = "default",
        top_k: int | None = None,
        mode: Literal["direct", "expand", "reflect"] = "expand",
    ) -> dict[str, Any]:
        """Search memory using the configured ranking strategy.

        Returns a dict with keys: packed_context, packed_token_count,
        fact_ids, facts, sessions, raw_messages.
        """
        effective_top_k = top_k or settings.QA_RERANK_TOP_K
        schema = f"task_{int(task_pk)}__{run_tag}"

        with self._sf() as db:
            db.execute(sa_text(f"SET LOCAL search_path TO {schema}, public"))
            return _retrieval.search(
                question=question,
                task_id=task_pk,
                db=db,
                embed_client=self._embed_client,
                http_client=self._http_client,
                top_k=effective_top_k,
                strategy=self._strategy,
                mode=mode,
            )

    @property
    def session_factory(self):
        return self._sf

    @property
    def embed_client(self):
        return self._embed_client

    @property
    def http_client(self):
        return self._http_client

    def search_ssa(
        self,
        *,
        task_pk: int,
        question: str,
        run_tag: str = "default",
    ) -> str:
        """Retrieve the single most relevant raw session for SSA questions.

        Two-path voting:
          - Path A: BM25 on assistant messages (rewritten query) → session of top hit
          - Path B: BM25 on session summaries (rewritten query) → top session

        Both paths nominate a session_id; if they agree that session wins.
        If they disagree, Path B (session summary) takes precedence since
        summaries are semantically denser and more stable.
        """
        schema = f"task_{int(task_pk)}__{run_tag}"
        with self._sf() as db:
            db.execute(sa_text(f"SET LOCAL search_path TO {schema}, public"))

            rewritten = rewrite_query(question, self._http_client)

            # Path A: top assistant message → its session
            msg_hits = bm25_search_messages(
                rewritten, task_pk, db, limit=20, speaker="assistant"
            )
            path_a_session_id = msg_hits[0].session_id if msg_hits else None

            # Path B: top session summary → its session
            sess_hits = bm25_search_sessions(rewritten, task_pk, db, limit=1)
            path_b_session_id = sess_hits[0].session_id if sess_hits else None

            # Resolve: agreement → either; disagree → prefer Path B
            best_session_id = path_b_session_id or path_a_session_id
            if best_session_id is None:
                return ""

            # Fetch session date
            row = db.execute(
                sa_text("SELECT session_time_raw FROM chat_sessions WHERE id = :sid"),
                {"sid": best_session_id},
            ).fetchone()
            date_label = row[0] if row and row[0] else f"session {best_session_id}"

            # Fetch ALL original messages, untruncated
            msgs = db.execute(
                sa_text(
                    "SELECT speaker, content FROM chat_messages"
                    " WHERE session_id = :sid ORDER BY message_time, id"
                ),
                {"sid": best_session_id},
            ).fetchall()

        lines = [f"### Session ({date_label})"]
        for speaker, content in msgs:
            lines.append(f"[{speaker}]: {content}")
        return "\n".join(lines)

    def cleanup(self) -> None:
        """Release all resources."""
        for resource in (self._embed_client, self._http_client):
            try:
                resource.close()
            except Exception:
                pass
        try:
            self._engine.dispose()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup()
