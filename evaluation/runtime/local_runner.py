"""In-process memory ingestion runner — bypasses the HTTP API layer.

Each ``LocalRunner`` instance owns an isolated per-run SQLAlchemy engine,
embedding client, and session workflow.  One worker thread = one runner
= one connection pool (``pool_size=1``), so there is no cross-thread
contention on DB connections.

Usage (inside a worker thread)::

    runner = LocalRunner(task_pk=42, run_tag="exp_a")
    try:
        asyncio.run(runner.ingest_session(...))
        asyncio.run(runner.finalize())
    finally:
        runner.cleanup()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.agents.retry import set_current_task
from membrain.config import settings
from membrain.infra.checkpoint import (
    clear_ingestion_tables,
    load_checkpoint_meta,
    restore_checkpoint,
    save_checkpoint,
)
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.db import create_run_engine, init_run_schema
from membrain.infra.models.memory import SessionSummaryModel
from membrain.memory.application.session_memory_workflow import (
    build_session_memory_workflow,
)

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS_DIR = str(_PROJECT_ROOT / "manifests")


@dataclass
class IngestResult:
    """Return value of :meth:`LocalRunner.ingest_session`."""

    session_number: int
    batches_processed: int
    entity_count: int
    fact_count: int
    summarized: bool


class LocalRunner:
    """In-process memory ingestion runner (one per worker thread)."""

    def __init__(self, task_pk: int, run_tag: str) -> None:
        self.task_pk = task_pk
        self.run_tag = run_tag

        self._engine = create_run_engine(task_pk, run_tag)
        init_run_schema(self._engine, task_pk, run_tag)
        self._sf = sessionmaker(bind=self._engine)
        self._embed_client = EmbeddingClient()
        self._registry = TaskRegistry(_MANIFESTS_DIR)
        self._factory = AgentFactory(
            self._registry, settings.LLM_API_URL, settings.LLM_API_KEY
        )
        self._workflow = build_session_memory_workflow(
            session_factory=self._sf,
            embed_client=self._embed_client,
            registry=self._registry,
            factory=self._factory,
        )

        # Load agent_profile from DB
        with self._sf() as db:
            from membrain.infra.models.dataset import TaskModel

            row = db.query(TaskModel.agent_profile).filter_by(id=self.task_pk).first()
            self.agent_profile: str | None = row[0] if row else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def summarize_session_only(
        self,
        messages: list[dict],
        session_number: int,
        session_pk: int,
        session_time: str = "",
    ) -> bool:
        """Summarize a session without ingesting facts (idempotent).

        Raises on LLM failure so the caller's task-level retry can take over.
        """
        set_current_task(str(self.task_pk))
        if not messages:
            return False
        await self._workflow.summarize_session(
            self.task_pk,
            session_pk,
            messages,
            session_time,
            profile=self.agent_profile,
        )
        return True

    async def ingest_session(
        self,
        messages: list[dict],
        session_number: int,
    ) -> IngestResult:
        """Ingest a full session: split → batch ingest (no summarize).

        Summarization is handled separately via summarize_session_only().
        """
        set_current_task(str(self.task_pk))

        if not messages:
            return IngestResult(
                session_number=session_number,
                batches_processed=0,
                entity_count=0,
                fact_count=0,
                summarized=False,
            )

        result = await self._workflow.ingest_session(
            task_pk=self.task_pk,
            messages=messages,
            session_number=session_number,
            profile=self.agent_profile,
        )

        # Save checkpoint once per session
        save_checkpoint(
            self.task_pk,
            self.run_tag,
            result.last_batch_index,
            session_number,
            result.batches_processed - 1,
            result.last_batch_id,
            pass_number=2,
        )

        return IngestResult(
            session_number=session_number,
            batches_processed=result.batches_processed,
            entity_count=result.entity_count,
            fact_count=result.fact_count,
            summarized=False,
        )

    def load_checkpoint(self) -> dict | None:
        """Load checkpoint metadata (returns None if no checkpoint)."""
        return load_checkpoint_meta(self.task_pk, self.run_tag)

    def restore_from_checkpoint(self, meta: dict) -> None:
        """Restore DB state from checkpoint and reset batch counter."""
        restore_checkpoint(self.task_pk, self.run_tag)
        if meta["pass"] == 2:
            self._workflow.set_batch_counter(meta["batch_index"] + 1)

    def get_session_summary(self, session_pk: int) -> str | None:
        """Return the summary content for a session, or None if not found."""
        with self._sf() as db:
            row = (
                db.query(SessionSummaryModel.content)
                .filter_by(task_id=self.task_pk, session_id=session_pk)
                .first()
            )
            return row[0] if row else None

    def save_summary_checkpoint(self, session_number: int) -> None:
        """Save a pass-1 checkpoint after a session summary."""
        save_checkpoint(
            self.task_pk,
            self.run_tag,
            0,
            session_number,
            0,
            "",
            pass_number=1,
        )

    def clear_ingestion_data(self) -> None:
        """Truncate all Pass-2 tables while preserving session summaries."""
        clear_ingestion_tables(self.task_pk, self.run_tag)
        self._workflow.set_batch_counter(0)

    def clear_all_summaries(self) -> None:
        """Delete all session summaries for this task."""
        with self._sf() as db:
            db.query(SessionSummaryModel).filter_by(task_id=self.task_pk).delete()
            db.commit()

    def cleanup(self) -> None:
        """Release all resources (engine, embedding client)."""
        try:
            self._embed_client.close()
        except Exception:
            pass
        try:
            self._engine.dispose()
        except Exception:
            pass
