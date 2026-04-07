"""Application-layer orchestration for entity-tree updates."""

from __future__ import annotations

from membrain.infra.persistence.entity_tree_store import EntityTreeStore
from membrain.memory.core.entity_tree.pipeline import compute_entity_tree_updates


class EntityTreeUpdater:
    def __init__(self, store: EntityTreeStore) -> None:
        self._store = store

    async def update(
        self,
        task_id: int,
        batch_id: str,
        embed_client,
        registry,
        factory,
    ) -> list[str]:
        touched_entity_ids = self._store.find_touched_entities(task_id, batch_id)
        if not touched_entity_ids:
            return []

        with self._store.lock_entities(task_id, touched_entity_ids) as db:
            state = self._store.load_update_state(
                task_id,
                batch_id,
                touched_entity_ids=touched_entity_ids,
                db=db,
            )
            if not state.targets:
                return []

            result = await compute_entity_tree_updates(
                task_id=state.task_id,
                targets=state.targets,
                embed_client=embed_client,
                registry=registry,
                factory=factory,
            )
            self._store.apply_updates(task_id, state, result, db=db)
            db.commit()
        return result.profiled_entities
