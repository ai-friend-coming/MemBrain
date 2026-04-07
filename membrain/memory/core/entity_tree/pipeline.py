"""In-memory entity-tree update pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.config import settings
from membrain.memory.core.entity_tree.model import EntityTree

log = logging.getLogger(__name__)


@dataclass
class EntitySnapshot:
    entity_id: str
    canonical_ref: str
    desc: str
    desc_embedding: np.ndarray | None


@dataclass
class FactSnapshot:
    id: int
    text: str
    text_embedding: np.ndarray | None


@dataclass
class EntityTreeUpdateTarget:
    entity_id: str
    entity: EntitySnapshot | None
    tree: EntityTree | None
    new_facts: list[FactSnapshot]


@dataclass
class EntityTreeUpdateResult:
    profiled_entities: list[str]
    trees_by_entity: dict[str, EntityTree]
    fact_embedding_updates: dict[int, list[float]]


async def compute_entity_tree_updates(
    task_id: int,
    targets: list[EntityTreeUpdateTarget],
    embed_client,
    registry: TaskRegistry,
    factory: AgentFactory,
) -> EntityTreeUpdateResult:
    from membrain.memory.core.entity_tree.audit import run_budgeted_audit
    from membrain.memory.core.entity_tree.propagate import (
        batch_embed_descriptions,
        batch_recompute_centroids,
        propagate_with_reachability_gate,
    )
    from membrain.memory.core.entity_tree.routing import route_facts
    from membrain.memory.core.entity_tree.tree_ops import (
        _create_root,
        _tree_is_flat,
        attach_all,
    )

    profiled_entities: list[str] = []
    trees_by_entity: dict[str, EntityTree] = {}
    fact_embedding_updates: dict[int, list[float]] = {}

    for target in targets:
        if not target.new_facts:
            continue

        entity_ref = (
            target.entity.canonical_ref
            if target.entity is not None
            else target.entity_id
        )
        tree = target.tree
        if tree is None:
            tree = _create_root(task_id, target.entity_id)

        for fact in target.new_facts:
            if fact.text_embedding is not None:
                continue
            fact.text_embedding = np.array(embed_client.embed_single(fact.text))
            fact_embedding_updates[fact.id] = fact.text_embedding.tolist()

        routing_result, _, target_nodes = route_facts(tree, target.new_facts)
        phase1_triggers = attach_all(tree, routing_result, target_nodes)

        for node_key, routed_facts in routing_result.items():
            target_node = target_nodes[node_key]
            label = (target_node.description or "(root)")[:60]
            for fact in routed_facts:
                log.info(
                    "    [tree:%s] attach -> '%s': %s",
                    entity_ref,
                    label,
                    fact.text[:80],
                )

        if _tree_is_flat(tree) and tree.root.support <= settings.AUDIT_DOWN_WARMUP_MIN:
            if target.entity is not None and target.entity.desc:
                tree.root.description = target.entity.desc
                if target.entity.desc_embedding is not None:
                    tree.root.description_embedding = (
                        target.entity.desc_embedding.copy()
                    )
            log.info(
                "    [tree:%s] warmup skip (flat, %d leaves)",
                entity_ref,
                tree.root.support,
            )
            trees_by_entity[target.entity_id] = tree
            profiled_entities.append(target.entity_id)
            continue

        await run_budgeted_audit(tree, embed_client, registry, factory)
        await propagate_with_reachability_gate(
            tree,
            set(phase1_triggers.keys()),
            registry,
            factory,
        )
        batch_embed_descriptions(tree, embed_client)
        batch_recompute_centroids(tree)

        trees_by_entity[target.entity_id] = tree
        profiled_entities.append(target.entity_id)

    return EntityTreeUpdateResult(
        profiled_entities=profiled_entities,
        trees_by_entity=trees_by_entity,
        fact_embedding_updates=fact_embedding_updates,
    )
