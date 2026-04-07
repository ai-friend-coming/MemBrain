"""Phase 1: Embedding top-down routing with _check_reachability_local — zero LLM calls.

Route facts from root to aspect nodes via cosine similarity, with reachability checks.
"""

from __future__ import annotations

import numpy as np

from membrain.config import settings
from membrain.memory.core.entity_tree.model import EntityTree, TreeNode
from membrain.memory.core.entity_tree.similarity import cosine_similarity


def route_facts(
    tree: EntityTree,
    new_facts: list,
) -> tuple[dict[int, list], set[TreeNode], dict[int, TreeNode]]:
    """Route new facts to aspect nodes using embedding similarity.

    Args:
        tree: The entity tree.
        new_facts: List of FactModel-like objects with .id, .text_embedding.

    Returns:
        routing_result: dict mapping id(aspect_node) -> list of facts
        affected_subtree: set of all nodes from routing targets up to root
    """
    routing_result: dict[int, list] = {}
    target_nodes: dict[int, TreeNode] = {}

    for fact in new_facts:
        fact_emb = fact.text_embedding
        if not isinstance(fact_emb, np.ndarray):
            if hasattr(fact_emb, "to_numpy"):
                fact_emb = np.array(fact_emb.to_numpy())
            else:
                fact_emb = np.array(fact_emb)

        target = _route_single(tree.root, fact_emb)
        node_key = id(target)
        routing_result.setdefault(node_key, []).append(fact)
        target_nodes[node_key] = target

    affected: set[TreeNode] = set()
    for node in target_nodes.values():
        cur = node
        while cur is not None:
            if cur in affected:
                break
            affected.add(cur)
            cur = cur.parent

    return routing_result, affected, target_nodes


def _score_child(fact_emb: np.ndarray, child: TreeNode) -> float:
    """Score a child node for routing: blend description + centroid for aspects."""
    if child.node_type == "leaf":
        if child.fact_embedding is not None:
            return cosine_similarity(fact_emb, child.fact_embedding)
        return -1.0

    desc_sim = -1.0
    if child.description_embedding is not None:
        desc_sim = cosine_similarity(fact_emb, child.description_embedding)

    cent_sim = -1.0
    if child.subtree_centroid is not None:
        cent_sim = cosine_similarity(fact_emb, child.subtree_centroid)

    if desc_sim >= 0 and cent_sim >= 0:
        alpha = settings.ALPHA_DESC
        return alpha * desc_sim + (1 - alpha) * cent_sim
    return max(desc_sim, cent_sim)


def _route_single(root: TreeNode, fact_emb: np.ndarray) -> TreeNode:
    """Route a single fact from root downward using blended scoring."""
    current = root
    while True:
        children = [c for c in current.children if not c._removed]
        if not children:
            break

        best_sim = -1.0
        best_node = current
        if current.description_embedding is not None:
            best_sim = cosine_similarity(fact_emb, current.description_embedding)

        for child in children:
            sim = _score_child(fact_emb, child)
            if sim > best_sim:
                best_sim = sim
                best_node = child

        if best_node is current:
            break
        if best_node.node_type == "leaf":
            break
        current = best_node

    return current


def _check_reachability_local(
    node: TreeNode, trigger_embeddings: list[np.ndarray]
) -> bool:
    """在节点的父层级，检查所有触发嵌入是否仍路由到该节点。"""
    parent = node.parent
    if parent is None:
        return True
    siblings = [c for c in parent.children if not c._removed]
    if len(siblings) <= 1:
        return True
    for emb in trigger_embeddings:
        best = max(
            siblings,
            key=lambda s: cosine_similarity(
                emb,
                s.description_embedding
                if s.description_embedding is not None
                else (
                    s.fact_embedding
                    if s.fact_embedding is not None
                    else np.zeros_like(emb)
                ),
            ),
        )
        if best is not node:
            return False
    return True
