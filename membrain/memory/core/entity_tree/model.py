"""Pure in-memory entity-tree structures."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(eq=False)
class TreeNode:
    node_type: str = "aspect"
    parent: TreeNode | None = None
    children: list[TreeNode] = field(default_factory=list)
    fact_id: int | None = None
    fact_text: str | None = None
    fact_embedding: np.ndarray | None = None
    description: str | None = None
    description_embedding: np.ndarray | None = None
    dirty: bool = False
    _removed: bool = False
    uncertainty_score: float = 0.0
    _embedding_stale: bool = False
    support: int = 0
    fresh_count: int = 0
    subtree_centroid: np.ndarray | None = None
    _centroid_stale: bool = False


class EntityTree:
    """In-memory entity tree plus transient mutation tracking."""

    def __init__(self, task_id: int, entity_id: str, root: TreeNode):
        self.task_id = task_id
        self.entity_id = entity_id
        self.root = root
        self.leaf_index: dict[int, TreeNode] = {}
        self._rebuild_leaf_index()

    def _rebuild_leaf_index(self) -> None:
        self.leaf_index.clear()
        self._walk_leaves(self.root)

    def _walk_leaves(self, node: TreeNode) -> None:
        if node.node_type == "leaf" and node.fact_id is not None:
            self.leaf_index[node.fact_id] = node
        for child in node.children:
            self._walk_leaves(child)

    def get_all_leaf_embeddings(self) -> list[tuple[int, np.ndarray]]:
        return [
            (fact_id, leaf.fact_embedding)
            for fact_id, leaf in self.leaf_index.items()
            if leaf.fact_embedding is not None
        ]

    def find_leaf_by_fact_id(self, fact_id: int) -> TreeNode | None:
        return self.leaf_index.get(fact_id)
