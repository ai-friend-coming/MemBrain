"""Application-layer batch ingestion workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import uuid_utils as uuid

from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.persistence.memory_ingest_store import MemoryIngestStore
from membrain.memory.application.entity_tree_updater import EntityTreeUpdater
from membrain.memory.application.message_text import format_lines

log = logging.getLogger(__name__)


@dataclass
class BatchResult:
    batch_index: int
    batch_id: str
    entities: list[dict]
    facts: list[dict]
    decisions: list[dict]
    profiled_entities: list[str] | None = None


class BatchIngester:
    """Use-case object for a single extraction batch."""

    def __init__(
        self,
        ingest_store: MemoryIngestStore,
        tree_updater: EntityTreeUpdater,
        embed_client: EmbeddingClient,
        registry: TaskRegistry,
        factory: AgentFactory,
    ) -> None:
        self._ingest_store = ingest_store
        self._tree_updater = tree_updater
        self._embed_client = embed_client
        self._registry = registry
        self._factory = factory
        self._batch_counter = 0

    def set_batch_counter(self, value: int) -> None:
        self._batch_counter = value

    async def ingest_batch(
        self,
        task_pk: int,
        messages: list[dict],
        context_size: int = 0,
        session_number: int | None = None,
        profile: str | None = None,
    ) -> BatchResult:
        """Ingest a single batch of messages into memory."""

        batch_id = str(uuid.uuid7())
        batch_index = self._batch_counter
        self._batch_counter += 1

        context_messages = messages[:context_size] if context_size else []
        extract_messages = messages[context_size:]
        context_text = format_lines(context_messages) if context_messages else ""
        messages_text = format_lines(extract_messages)

        return await self._run_batch(
            batch_index=batch_index,
            messages_text=messages_text,
            context_text=context_text,
            task_pk=task_pk,
            batch_id=batch_id,
            session_number=session_number,
            profile=profile,
        )

    async def _run_batch(
        self,
        batch_index: int,
        messages_text: str,
        context_text: str,
        task_pk: int,
        batch_id: str,
        session_number: int | None = None,
        profile: str | None = None,
    ) -> BatchResult:
        from membrain.memory.application.ingest_workflow import get_workflow

        workflow = get_workflow(
            profile,
            ingest_store=self._ingest_store,
            tree_updater=self._tree_updater,
            embed_client=self._embed_client,
            registry=self._registry,
            factory=self._factory,
        )
        return await workflow.run_batch(
            batch_index, messages_text, context_text, task_pk, batch_id, session_number
        )
