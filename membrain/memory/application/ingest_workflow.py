"""Ingest workflow strategy pattern for batch processing."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from pydantic_ai import Agent, ModelRetry, RunContext

from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.agents.retry import run_agent_with_retry
from membrain.config import settings
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.persistence.batch_writer import _ENTITY_BRACKET_RE
from membrain.infra.persistence.memory_ingest_store import MemoryIngestStore
from membrain.memory.application.batch_ingestor import BatchResult
from membrain.memory.application.entity_tree_updater import EntityTreeUpdater
from membrain.memory.application.message_text import build_known_entities_text
from membrain.memory.core.entity_tree.text import render_fact_text

log = logging.getLogger(__name__)

_registered: set[int] = set()


def _build_decisions_from_extraction(entities: list[dict]) -> list[dict]:
    return [
        {
            "batch_ref": entity["ref"],
            "action": "create",
            "target_ref": None,
            "canonical_ref": entity["ref"],
            "updated_desc": entity.get("desc", ""),
        }
        for entity in entities
    ]


def _apply_fact_generator_fallback(
    facts: list[dict],
    allowed_refs: set[str],
) -> tuple[list[str], list[dict]]:
    filtered_facts = []
    for fact in facts:
        refs = set(_ENTITY_BRACKET_RE.findall(fact["text"]))
        if refs <= allowed_refs:
            filtered_facts.append(fact)

    used: set[str] = set()
    for fact in filtered_facts:
        used.update(_ENTITY_BRACKET_RE.findall(fact["text"]))
    return sorted(used), filtered_facts


def _build_entity_descs_from_facts(
    entity_names: list[str],
    facts: list[dict],
) -> list[dict]:
    entity_facts: dict[str, list[str]] = {name: [] for name in entity_names}
    for fact in facts:
        refs = set(_ENTITY_BRACKET_RE.findall(fact["text"]))
        for ref in refs:
            if ref in entity_facts:
                entity_facts[ref].append(fact["text"])

    return [
        {
            "ref": name,
            "desc": "; ".join(render_fact_text(fact) for fact in entity_facts[name]),
        }
        for name in entity_names
        if entity_facts[name]
    ]


async def _batch_canonicalize(
    merge_candidates: list[dict],
    registry: TaskRegistry,
    factory: AgentFactory,
    profile: str | None = None,
) -> dict[int, dict]:
    entities_input = [
        {
            "idx": index,
            "old_canonical_name": decision.get("old_canonical_ref", ""),
            "all_aliases": decision.get("all_aliases", [decision["batch_ref"]]),
            "old_description": decision.get("old_description", ""),
            "new_facts": decision.get("updated_desc", ""),
        }
        for index, decision in enumerate(merge_candidates)
    ]
    entities_json = json.dumps(entities_input, ensure_ascii=False)
    try:
        agent, agent_settings = factory.get_agent(
            "entity-canonicalizer", profile=profile
        )
        prompts = registry.render_prompts(
            "entity-canonicalizer",
            profile=profile,
            entities_json=entities_json,
        )
        result = await run_agent_with_retry(
            agent,
            instructions=prompts,
            model_settings=agent_settings,
        )
        canon_map = {
            item.idx: {
                "canonical_ref": item.canonical_ref,
                "merged_desc": item.merged_desc,
            }
            for item in result.output.results
        }
        missing = [i for i in range(len(merge_candidates)) if i not in canon_map]
        if missing:
            log.warning(
                "entity-canonicalizer skipped %d/%d idx: %s",
                len(missing),
                len(merge_candidates),
                missing,
            )
        return canon_map
    except Exception:
        log.warning(
            "entity-canonicalizer failed, keeping original values", exc_info=True
        )
        return {}


def _register_entity_coverage_validator(agent: Agent) -> None:
    if id(agent) in _registered:
        return
    _registered.add(id(agent))

    @agent.output_validator
    async def validate_entity_coverage(ctx: RunContext[dict], result) -> object:
        allowed_refs: set[str] = ctx.deps.get("allowed_entity_refs", set())
        if not allowed_refs:
            return result
        fact_refs: set[str] = set()
        for fact in result.facts:
            fact_refs.update(_ENTITY_BRACKET_RE.findall(fact.text))
        illegal = fact_refs - allowed_refs
        if illegal:
            raise ModelRetry(
                f"These bracketed refs in your facts are not in the entity list: {sorted(illegal)}. "
                f"The allowed refs are: {sorted(allowed_refs)}. "
                f"Fix each fact to use only refs from that list, or remove the fact."
            )
        return result


class IngestWorkflow(ABC):
    """Abstract base class for ingest workflows."""

    def __init__(
        self,
        ingest_store: MemoryIngestStore,
        tree_updater: EntityTreeUpdater,
        embed_client: EmbeddingClient,
        registry: TaskRegistry,
        factory: AgentFactory,
        profile: str | None,
    ):
        self._ingest_store = ingest_store
        self._tree_updater = tree_updater
        self._embed_client = embed_client
        self._registry = registry
        self._factory = factory
        self._profile = profile

    @abstractmethod
    async def run_batch(
        self,
        batch_index: int,
        messages_text: str,
        context_text: str,
        task_pk: int,
        batch_id: str,
        session_number: int | None,
    ) -> BatchResult:
        """Execute the batch ingestion workflow."""
        ...

    async def _extract_entities(
        self,
        messages_text: str,
        context_text: str,
        task_pk: int,
    ) -> list[str]:
        """Stage 1: Entity extraction with two-pass refinement."""
        entity_extractor, extractor_settings = self._factory.get_agent(
            "entity-extractor",
            profile=self._profile,
        )
        prompts = self._registry.render_prompts(
            "entity-extractor",
            profile=self._profile,
            context_messages=context_text,
            messages_json=messages_text,
            entity_context="",
        )
        result = await run_agent_with_retry(
            entity_extractor,
            instructions=prompts,
            model_settings=extractor_settings,
        )
        entity_names = result.output.entities
        log.debug("entity-extractor pass-1 -> %d entities", len(entity_names))

        entity_context = self._ingest_store.load_extraction_context(
            entity_names=entity_names,
            task_id=task_pk,
            embed_client=self._embed_client,
        )
        if entity_context:
            known_entities = build_known_entities_text(entity_context)
            prompts = self._registry.render_prompts(
                "entity-extractor",
                profile=self._profile,
                context_messages=context_text,
                messages_json=messages_text,
                entity_context=known_entities,
            )
            result = await run_agent_with_retry(
                entity_extractor,
                instructions=prompts,
                model_settings=extractor_settings,
            )
            entity_names = result.output.entities
            log.debug(
                "entity-extractor pass-2 -> %d entities (ctx=%d)",
                len(entity_names),
                len(entity_context),
            )
        else:
            log.debug("entity-extractor pass-2 skipped (no candidates)")

        return entity_names

    async def _generate_facts(
        self,
        entity_names: list[str],
        messages_text: str,
        context_text: str,
    ) -> tuple[list[str], list[dict]]:
        """Stage 2: Fact generation with entity coverage validation."""
        fact_generator, generator_settings = self._factory.get_agent(
            "fact-generator",
            profile=self._profile,
        )
        _register_entity_coverage_validator(fact_generator)
        entity_list_json = json.dumps(entity_names, ensure_ascii=False)
        prompts = self._registry.render_prompts(
            "fact-generator",
            profile=self._profile,
            entity_list_json=entity_list_json,
            context_messages=context_text,
            messages_json=messages_text,
        )
        allowed_refs = set(entity_names)

        try:
            result = await run_agent_with_retry(
                fact_generator,
                instructions=prompts,
                model_settings=generator_settings,
                deps={"allowed_entity_refs": allowed_refs},
            )
            facts = [f.model_dump() for f in result.output.facts]
        except Exception as exc:
            log.warning(
                "fact-generator failed (illegal refs or schema violation), retrying without entity constraint: %s",
                exc,
            )
            try:
                result = await run_agent_with_retry(
                    fact_generator,
                    instructions=prompts,
                    model_settings=generator_settings,
                    deps={"allowed_entity_refs": set()},
                )
                facts = [f.model_dump() for f in result.output.facts]
                entity_names, facts = _apply_fact_generator_fallback(
                    facts,
                    allowed_refs,
                )
            except Exception:
                log.exception("fact-generator retry also failed")
                entity_names, facts = [], []

        log.debug(
            "fact-generator -> %d entities, %d facts", len(entity_names), len(facts)
        )
        log.info(
            "    [extract] entities (%d): %s",
            len(entity_names),
            ", ".join(entity_names) if entity_names else "(none)",
        )
        for fact in facts:
            log.info("    [extract] fact: %s", fact["text"])

        return entity_names, facts

    async def _resolve_entities(
        self,
        entities: list[dict],
        facts: list[dict],
        task_pk: int,
        batch_id: str,
    ) -> list[dict]:
        """Stage 3: Entity resolution and canonicalization."""
        decisions = _build_decisions_from_extraction(entities)
        resolved = await self._ingest_store.resolve_entity_decisions(
            task_id=task_pk,
            decisions=decisions,
            embed_client=self._embed_client,
            registry=self._registry,
            factory=self._factory,
            profile=self._profile,
        )

        merge_count = sum(1 for d in resolved.decisions if d["action"] == "merge")
        create_count = len(resolved.decisions) - merge_count
        log.info(
            "    [resolve] %d decisions (merge=%d, create=%d)",
            len(resolved.decisions),
            merge_count,
            create_count,
        )
        for decision in resolved.decisions:
            if decision["action"] == "merge":
                log.debug(
                    "    [resolve] merge: %s -> entity_id=%s",
                    decision["batch_ref"],
                    decision.get("target_entity_id", "?"),
                )

        if settings.CANONICALIZER_ENABLED:
            canon_candidates = [
                d for d in resolved.decisions if d["action"] in ("merge", "create")
            ]
            if canon_candidates:
                canon_map = await _batch_canonicalize(
                    canon_candidates,
                    self._registry,
                    self._factory,
                    profile=self._profile,
                )
                for index, decision in enumerate(canon_candidates):
                    if index in canon_map:
                        decision["canonical_ref"] = canon_map[index]["canonical_ref"]
                        decision["updated_desc"] = canon_map[index]["merged_desc"]
                        log.debug(
                            "    [canonicalize] %s -> %s | %s",
                            decision["batch_ref"],
                            canon_map[index]["canonical_ref"],
                            (canon_map[index]["merged_desc"] or "")[:100],
                        )

                merge_count = sum(
                    1 for decision in canon_candidates if decision["action"] == "merge"
                )
                create_count = len(canon_candidates) - merge_count
                log.info(
                    "    entity-canonicalizer -> %d/%d updated (merge=%d, create=%d)",
                    len(canon_map),
                    len(canon_candidates),
                    merge_count,
                    create_count,
                )

        return resolved.decisions

    async def _persist_and_update_tree(
        self,
        facts: list[dict],
        decisions: list[dict],
        task_pk: int,
        batch_id: str,
        batch_index: int,
        session_number: int | None,
    ) -> list[str] | None:
        """Stage 4: Persist to database and update entity tree."""
        self._ingest_store.persist_batch(
            task_id=task_pk,
            batch_id=batch_id,
            facts=facts,
            decisions=decisions,
            embed_client=self._embed_client,
            batch_index=batch_index,
            session_number=session_number,
        )
        log.debug("DB write complete (batch_id=%s)", batch_id)

        profiled_entities = await self._tree_updater.update(
            task_id=task_pk,
            batch_id=batch_id,
            embed_client=self._embed_client,
            registry=self._registry,
            factory=self._factory,
        )
        if profiled_entities:
            log.debug("entity tree -> %d tree(s) updated", len(profiled_entities))

        return profiled_entities or None


class DefaultIngestWorkflow(IngestWorkflow):
    """Default workflow: full 4-stage pipeline."""

    async def run_batch(
        self,
        batch_index: int,
        messages_text: str,
        context_text: str,
        task_pk: int,
        batch_id: str,
        session_number: int | None,
    ) -> BatchResult:
        # Stage 1: Entity extraction
        entity_names = await self._extract_entities(
            messages_text, context_text, task_pk
        )

        # Stage 2: Fact generation
        entity_names, facts = await self._generate_facts(
            entity_names, messages_text, context_text
        )

        # Stage 3: Entity resolution
        entities = _build_entity_descs_from_facts(entity_names, facts)
        decisions = await self._resolve_entities(entities, facts, task_pk, batch_id)

        # Stage 4: Persistence & tree update
        profiled_entities = await self._persist_and_update_tree(
            facts, decisions, task_pk, batch_id, batch_index, session_number
        )

        return BatchResult(
            batch_index=batch_index,
            batch_id=batch_id,
            entities=entities,
            facts=facts,
            decisions=decisions,
            profiled_entities=profiled_entities,
        )


class PersonaMemIngestWorkflow(IngestWorkflow):
    """PersonaMem workflow: skip entity extraction and LLM resolution, use fixed ["User"]."""

    async def run_batch(
        self,
        batch_index: int,
        messages_text: str,
        context_text: str,
        task_pk: int,
        batch_id: str,
        session_number: int | None,
    ) -> BatchResult:
        # Stage 1: Fixed entity (skip extraction)
        entity_names = ["User"]
        log.debug("entity-extractor skipped (personamemv2) -> 1 entity")

        # Stage 2: Fact generation
        entity_names, facts = await self._generate_facts(
            entity_names, messages_text, context_text
        )

        # Stage 3: Simplified entity resolution — exact DB lookup, no LLM
        entities = _build_entity_descs_from_facts(entity_names, facts)
        decisions = _build_decisions_from_extraction(entities)
        if decisions:
            context = self._ingest_store.load_extraction_context(
                entity_names=["User"],
                task_id=task_pk,
                embed_client=self._embed_client,
            )
            user_ctx = next((c for c in context if c.canonical_ref == "User"), None)
            if user_ctx:
                decisions[0]["action"] = "merge"
                decisions[0]["target_entity_id"] = user_ctx.entity_id
            log.info(
                "    [resolve] 1 decision (%s)",
                decisions[0]["action"],
            )

        # Stage 4: Persistence & tree update
        profiled_entities = await self._persist_and_update_tree(
            facts, decisions, task_pk, batch_id, batch_index, session_number
        )

        return BatchResult(
            batch_index=batch_index,
            batch_id=batch_id,
            entities=entities,
            facts=facts,
            decisions=decisions,
            profiled_entities=profiled_entities,
        )


_WORKFLOW_REGISTRY: dict[str | None, type[IngestWorkflow]] = {
    None: DefaultIngestWorkflow,
    "personamemv2": PersonaMemIngestWorkflow,
}


def get_workflow(
    profile: str | None,
    *,
    ingest_store: MemoryIngestStore,
    tree_updater: EntityTreeUpdater,
    embed_client: EmbeddingClient,
    registry: TaskRegistry,
    factory: AgentFactory,
) -> IngestWorkflow:
    """Get workflow instance for the given profile."""
    cls = _WORKFLOW_REGISTRY.get(profile, DefaultIngestWorkflow)
    return cls(ingest_store, tree_updater, embed_client, registry, factory, profile)
