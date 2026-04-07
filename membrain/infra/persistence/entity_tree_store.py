"""Persistence adapter for entity-tree updates."""

from __future__ import annotations

import zlib
from contextlib import nullcontext
from dataclasses import dataclass

import numpy as np

from membrain.infra.models.memory import FactModel
from membrain.infra.persistence.entity_tree_mapper import (
    EntityTreePersistenceState,
    batch_load_entity_trees,
    save_entity_tree,
)
from membrain.infra.queries.entity_tree import batch_get_entities
from membrain.infra.queries.facts import (
    batch_find_new_facts_not_in_tree,
    get_touched_entity_ids,
    load_facts,
)
from membrain.infra.transaction_manager import TransactionManager
from membrain.memory.core.entity_tree.pipeline import (
    EntitySnapshot,
    EntityTreeUpdateResult,
    EntityTreeUpdateTarget,
    FactSnapshot,
)


@dataclass
class EntityTreeUpdateState:
    task_id: int
    targets: list[EntityTreeUpdateTarget]
    persistence_by_entity: dict[str, EntityTreePersistenceState]


class EntityTreeStore:
    def __init__(self, transactions: TransactionManager) -> None:
        self._transactions = transactions

    @staticmethod
    def _lock_slot(entity_id: str) -> int:
        slot = zlib.crc32(entity_id.encode("utf-8"))
        if slot >= 2**31:
            slot -= 2**32
        return slot

    def find_touched_entities(self, task_id: int, batch_id: str) -> list[str]:
        with self._transactions.read() as db:
            return sorted(get_touched_entity_ids(db, task_id, batch_id))

    def lock_entities(self, task_id: int, entity_ids: list[str]):
        lock_keys = [
            (task_id, self._lock_slot(entity_id))
            for entity_id in sorted(set(entity_ids))
        ]
        return self._transactions.advisory_locks(lock_keys)

    def load_update_state(
        self,
        task_id: int,
        batch_id: str,
        touched_entity_ids: list[str] | None = None,
        *,
        db=None,
    ) -> EntityTreeUpdateState:
        ctx = nullcontext(db) if db is not None else self._transactions.read()
        with ctx as _db:
            if touched_entity_ids is None:
                touched_entity_ids = sorted(
                    get_touched_entity_ids(_db, task_id, batch_id)
                )
            if not touched_entity_ids:
                return EntityTreeUpdateState(
                    task_id=task_id,
                    targets=[],
                    persistence_by_entity={},
                )

            entities_map = batch_get_entities(_db, task_id, touched_entity_ids)
            loaded_trees = batch_load_entity_trees(_db, task_id, touched_entity_ids)
            new_facts_map = batch_find_new_facts_not_in_tree(
                _db, task_id, touched_entity_ids
            )

            all_new_fact_ids: list[int] = []
            for fact_ids in new_facts_map.values():
                all_new_fact_ids.extend(fact_ids)
            all_facts_list = load_facts(_db, all_new_fact_ids)
            facts_by_id = {fact.id: fact for fact in all_facts_list}

            targets: list[EntityTreeUpdateTarget] = []
            for entity_id in touched_entity_ids:
                entity_model = entities_map.get(entity_id)
                entity = None
                if entity_model is not None:
                    entity = EntitySnapshot(
                        entity_id=entity_model.entity_id,
                        canonical_ref=entity_model.canonical_ref,
                        desc=entity_model.desc or "",
                        desc_embedding=(
                            np.array(entity_model.desc_embedding.to_numpy())
                            if entity_model.desc_embedding is not None
                            else None
                        ),
                    )
                fact_ids = new_facts_map.get(entity_id, [])
                facts = [
                    FactSnapshot(
                        id=fact.id,
                        text=fact.text,
                        text_embedding=(
                            np.array(fact.text_embedding.to_numpy())
                            if fact.text_embedding is not None
                            else None
                        ),
                    )
                    for fact_id in fact_ids
                    if (fact := facts_by_id.get(fact_id)) is not None
                ]
                targets.append(
                    EntityTreeUpdateTarget(
                        entity_id=entity_id,
                        entity=entity,
                        tree=(
                            loaded_trees[entity_id].tree
                            if entity_id in loaded_trees
                            else None
                        ),
                        new_facts=facts,
                    )
                )

        return EntityTreeUpdateState(
            task_id=task_id,
            targets=targets,
            persistence_by_entity={
                entity_id: loaded.persistence
                for entity_id, loaded in loaded_trees.items()
            },
        )

    def apply_updates(
        self,
        task_id: int,
        update_state: EntityTreeUpdateState,
        result: EntityTreeUpdateResult,
        *,
        db=None,
    ) -> None:
        if not result.profiled_entities:
            return

        ctx = nullcontext(db) if db is not None else self._transactions.write()
        with ctx as _db:
            if result.fact_embedding_updates:
                facts = (
                    _db.query(FactModel)
                    .filter(FactModel.id.in_(result.fact_embedding_updates.keys()))
                    .all()
                )
                for fact in facts:
                    fact.text_embedding = result.fact_embedding_updates[fact.id]

            for entity_id in result.profiled_entities:
                tree = result.trees_by_entity.get(entity_id)
                if tree is not None:
                    save_entity_tree(
                        _db,
                        tree,
                        update_state.persistence_by_entity.get(entity_id),
                    )
