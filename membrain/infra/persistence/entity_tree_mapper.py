"""Map entity-tree domain models to and from ORM rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from membrain.infra.models.memory import EntityTreeNodeModel, FactModel, FactStatus
from membrain.memory.core.entity_tree.model import EntityTree, TreeNode


@dataclass
class EntityTreePersistenceState:
    node_db_ids: dict[int, int] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "EntityTreePersistenceState":
        return cls()

    def lookup(self, node: TreeNode) -> int | None:
        return self.node_db_ids.get(id(node))

    def remember(self, node: TreeNode, db_id: int) -> None:
        self.node_db_ids[id(node)] = db_id


@dataclass
class LoadedEntityTree:
    tree: EntityTree
    persistence: EntityTreePersistenceState


def batch_load_entity_trees(
    db: Session,
    task_id: int,
    entity_ids: list[str],
) -> dict[str, LoadedEntityTree]:
    """Load multiple entity trees in two queries (nodes + facts)."""

    if not entity_ids:
        return {}

    rows = (
        db.query(EntityTreeNodeModel)
        .filter(
            EntityTreeNodeModel.task_id == task_id,
            EntityTreeNodeModel.entity_id.in_(entity_ids),
        )
        .all()
    )
    if not rows:
        return {}

    leaf_fact_ids = [row.fact_id for row in rows if row.fact_id is not None]
    facts_by_id = _load_active_facts(db, leaf_fact_ids)

    rows_by_entity: dict[str, list[Any]] = {}
    for row in rows:
        rows_by_entity.setdefault(row.entity_id, []).append(row)

    result: dict[str, LoadedEntityTree] = {}
    for entity_id, entity_rows in rows_by_entity.items():
        loaded = _assemble_tree(task_id, entity_id, entity_rows, facts_by_id)
        if loaded is not None:
            result[entity_id] = loaded
    return result


def save_entity_tree(
    db: Session,
    tree: EntityTree,
    persistence: EntityTreePersistenceState | None = None,
) -> EntityTreePersistenceState:
    """Persist in-memory tree mutations back to DB."""

    state = persistence or EntityTreePersistenceState.empty()
    prior_node_db_ids = dict(state.node_db_ids)
    next_node_db_ids: dict[int, int] = {}
    existing_db_ids = _collect_existing_db_ids(tree.root, state)
    orm_map: dict[int, EntityTreeNodeModel] = {}
    if existing_db_ids:
        orm_nodes = (
            db.query(EntityTreeNodeModel)
            .filter(EntityTreeNodeModel.id.in_(existing_db_ids))
            .all()
        )
        orm_map = {node.id: node for node in orm_nodes}

    level: list[tuple[TreeNode, int | None]] = [(tree.root, None)]
    while level:
        new_nodes: list[tuple[TreeNode, EntityTreeNodeModel]] = []
        next_level_refs: list[tuple[TreeNode, TreeNode]] = []
        for node, parent_db_id in level:
            if node._removed:
                continue

            db_id = state.lookup(node)
            if db_id is None:
                db_node = EntityTreeNodeModel(
                    task_id=tree.task_id,
                    entity_id=tree.entity_id,
                    parent_id=parent_db_id,
                    node_type=node.node_type,
                    fact_id=node.fact_id,
                    description=node.description,
                    description_embedding=(
                        node.description_embedding.tolist()
                        if node.description_embedding is not None
                        else None
                    ),
                    uncertainty_score=node.uncertainty_score,
                    support=node.support,
                    fresh_count=node.fresh_count,
                    subtree_centroid=(
                        node.subtree_centroid.tolist()
                        if node.subtree_centroid is not None
                        else None
                    ),
                )
                db.add(db_node)
                new_nodes.append((node, db_node))
            else:
                db_node = orm_map.get(db_id)
                if db_node is not None:
                    db_node.parent_id = parent_db_id
                    db_node.node_type = node.node_type
                    db_node.description = node.description
                    db_node.description_embedding = (
                        node.description_embedding.tolist()
                        if node.description_embedding is not None
                        else None
                    )
                    db_node.uncertainty_score = node.uncertainty_score
                    db_node.support = node.support
                    db_node.fresh_count = node.fresh_count
                    db_node.subtree_centroid = (
                        node.subtree_centroid.tolist()
                        if node.subtree_centroid is not None
                        else None
                    )
                    next_node_db_ids[id(node)] = db_id

            for child in node.children:
                next_level_refs.append((child, node))

        db.flush()
        for tree_node, orm_node in new_nodes:
            next_node_db_ids[id(tree_node)] = orm_node.id

        level = [
            (child, next_node_db_ids.get(id(parent)))
            for child, parent in next_level_refs
            if not child._removed
        ]

    active_db_ids = set(next_node_db_ids.values())
    deleted_db_ids = set(prior_node_db_ids.values()) - active_db_ids
    if deleted_db_ids:
        active_parent_ids: set[int] = set()
        _collect_active_parent_ids(tree.root, active_parent_ids, next_node_db_ids)
        orphan_refs = active_parent_ids & deleted_db_ids
        if orphan_refs:
            raise RuntimeError(
                "BUG: active nodes reference pending-delete parents: "
                f"{orphan_refs}. Aborting to prevent cascade data loss."
            )

        db.query(EntityTreeNodeModel).filter(
            EntityTreeNodeModel.id.in_(deleted_db_ids)
        ).delete(synchronize_session=False)
        db.flush()

    state.node_db_ids = next_node_db_ids
    return state


def _load_active_facts(db: Session, fact_ids: list[int]) -> dict[int, FactModel]:
    if not fact_ids:
        return {}

    facts = (
        db.query(FactModel)
        .filter(
            FactModel.id.in_(fact_ids),
            FactModel.status == FactStatus.ACTIVE,
        )
        .all()
    )
    return {fact.id: fact for fact in facts}


def _assemble_tree(
    task_id: int,
    entity_id: str,
    rows: list[Any],
    facts_by_id: dict[int, FactModel],
) -> LoadedEntityTree | None:
    nodes_by_id: dict[int, TreeNode] = {}
    root_node: TreeNode | None = None
    persistence = EntityTreePersistenceState.empty()

    for row in rows:
        fact = facts_by_id.get(row.fact_id) if row.fact_id else None
        node = TreeNode(
            node_type=row.node_type,
            fact_id=row.fact_id,
            fact_text=fact.text if fact else None,
            fact_embedding=(
                np.array(fact.text_embedding.to_numpy())
                if fact and fact.text_embedding is not None
                else None
            ),
            description=row.description,
            description_embedding=(
                row.description_embedding.to_numpy()
                if row.description_embedding is not None
                else None
            ),
            uncertainty_score=getattr(row, "uncertainty_score", None) or 0.0,
            support=getattr(row, "support", None) or 0,
            fresh_count=getattr(row, "fresh_count", None) or 0,
            subtree_centroid=(
                row.subtree_centroid.to_numpy()
                if getattr(row, "subtree_centroid", None) is not None
                else None
            ),
        )
        nodes_by_id[row.id] = node
        persistence.remember(node, row.id)
        if row.node_type == "root":
            root_node = node

    if root_node is None:
        return None

    for row in rows:
        node = nodes_by_id[row.id]
        if row.parent_id is not None and row.parent_id in nodes_by_id:
            parent = nodes_by_id[row.parent_id]
            node.parent = parent
            parent.children.append(node)

    return LoadedEntityTree(
        tree=EntityTree(task_id, entity_id, root_node),
        persistence=persistence,
    )


def _collect_existing_db_ids(
    node: TreeNode,
    persistence: EntityTreePersistenceState,
) -> list[int]:
    result: list[int] = []
    queue = [node]
    while queue:
        current = queue.pop(0)
        if not current._removed:
            db_id = persistence.lookup(current)
            if db_id is not None:
                result.append(db_id)
            queue.extend(current.children)
    return result


def _collect_active_parent_ids(
    node: TreeNode,
    result: set[int],
    node_db_ids: dict[int, int],
) -> None:
    if node._removed:
        return
    if node.parent is not None:
        parent_db_id = node_db_ids.get(id(node.parent))
        if parent_db_id is not None:
            result.add(parent_db_id)
    for child in node.children:
        _collect_active_parent_ids(child, result, node_db_ids)
