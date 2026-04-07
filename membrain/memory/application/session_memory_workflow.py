"""Application-layer session ingestion workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from membrain.config import settings

log = logging.getLogger(__name__)
from membrain.infra.persistence.entity_tree_store import EntityTreeStore
from membrain.infra.persistence.memory_ingest_store import MemoryIngestStore
from membrain.infra.persistence.session_summary_store import SessionSummaryStore
from membrain.infra.transaction_manager import TransactionManager

from .batch_ingestor import BatchIngester, BatchResult
from .entity_tree_updater import EntityTreeUpdater
from .message_text import split_into_batches
from .session_summarizer import SessionSummarizer


@dataclass
class SessionIngestionResult:
    batches_processed: int
    entity_count: int
    fact_count: int
    last_batch_index: int | None = None
    last_batch_id: str | None = None


@dataclass
class SessionProcessResult:
    """Result of the unified per-session pipeline (summarize → ingest)."""

    batches_processed: int
    entity_count: int
    fact_count: int
    summarized: bool = False


class SessionMemoryWorkflow:
    """Orchestrate the memory use cases for a single session."""

    def __init__(
        self,
        batch_ingestor: BatchIngester,
        session_summarizer: SessionSummarizer,
    ) -> None:
        self._batch_ingestor = batch_ingestor
        self._session_summarizer = session_summarizer

    @property
    def batch_ingestor(self) -> BatchIngester:
        return self._batch_ingestor

    @property
    def session_summarizer(self) -> SessionSummarizer:
        return self._session_summarizer

    def set_batch_counter(self, value: int) -> None:
        self._batch_ingestor.set_batch_counter(value)

    async def ingest_batch(
        self,
        task_pk: int,
        messages: list[dict],
        context_size: int = 0,
        session_number: int | None = None,
        profile: str | None = None,
    ) -> BatchResult:
        return await self._batch_ingestor.ingest_batch(
            task_pk=task_pk,
            messages=messages,
            context_size=context_size,
            session_number=session_number,
            profile=profile,
        )

    async def ingest_session(
        self,
        task_pk: int,
        messages: list[dict],
        session_number: int | None = None,
        profile: str | None = None,
    ) -> SessionIngestionResult:
        if not messages:
            return SessionIngestionResult(
                batches_processed=0,
                entity_count=0,
                fact_count=0,
            )

        batches = split_into_batches(messages)
        total_entities = 0
        total_facts = 0
        prev_batch: list[dict] | None = None
        last_result: BatchResult | None = None

        for batch_index, batch in enumerate(batches):
            if batch_index > 0 and prev_batch:
                tail = prev_batch[-settings.EXTRACT_CONTEXT_TAIL_SIZE :]
                input_messages = tail + batch
                context_size = len(tail)
            else:
                input_messages = batch
                context_size = 0

            last_result = await self._batch_ingestor.ingest_batch(
                task_pk=task_pk,
                messages=input_messages,
                context_size=context_size,
                session_number=session_number,
                profile=profile,
            )
            total_entities += len(last_result.entities)
            total_facts += len(last_result.facts)
            prev_batch = batch

        return SessionIngestionResult(
            batches_processed=len(batches),
            entity_count=total_entities,
            fact_count=total_facts,
            last_batch_index=last_result.batch_index if last_result else None,
            last_batch_id=last_result.batch_id if last_result else None,
        )

    async def summarize_session(
        self,
        task_pk: int,
        session_id: int,
        session_messages: list[dict],
        session_time: str,
        profile: str | None = None,
    ) -> None:
        await self._session_summarizer.summarize_session(
            task_pk=task_pk,
            session_id=session_id,
            session_messages=session_messages,
            session_time=session_time,
            profile=profile,
        )

    async def process_session(
        self,
        task_pk: int,
        messages: list[dict],
        session_number: int | None = None,
        session_pk: int | None = None,
        session_time: str = "",
        profile: str | None = None,
    ) -> SessionProcessResult:
        """Unified per-session pipeline: summarize → ingest.

        - summarize failure: caught and logged, ``summarized=False``
        - ingest failure: propagated (critical step)
        """
        if not messages:
            return SessionProcessResult(
                batches_processed=0,
                entity_count=0,
                fact_count=0,
            )

        # 1. Summarize (non-fatal)
        summarized = False
        if session_pk is not None:
            try:
                await self.summarize_session(
                    task_pk,
                    session_pk,
                    messages,
                    session_time,
                    profile=profile,
                )
                summarized = True
            except Exception as exc:
                log.warning("session %s summarize failed: %s", session_number, exc)

        # 2. Ingest (fatal)
        result = await self.ingest_session(
            task_pk=task_pk,
            messages=messages,
            session_number=session_number,
            profile=profile,
        )

        return SessionProcessResult(
            batches_processed=result.batches_processed,
            entity_count=result.entity_count,
            fact_count=result.fact_count,
            summarized=summarized,
        )


def build_session_memory_workflow(
    session_factory,
    embed_client,
    registry,
    factory,
) -> SessionMemoryWorkflow:
    """Build the concrete session workflow for a runtime entry point."""

    transactions = TransactionManager(session_factory)
    return SessionMemoryWorkflow(
        batch_ingestor=BatchIngester(
            ingest_store=MemoryIngestStore(transactions),
            tree_updater=EntityTreeUpdater(EntityTreeStore(transactions)),
            embed_client=embed_client,
            registry=registry,
            factory=factory,
        ),
        session_summarizer=SessionSummarizer(
            summary_store=SessionSummaryStore(transactions),
            registry=registry,
            factory=factory,
        ),
    )
