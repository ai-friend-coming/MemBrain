"""Low-level tree structure operations: attach_all and node manipulation (pure in-memory, no DB/LLM)."""

from __future__ import annotations

import math

import numpy as np

from membrain.config import settings
from membrain.memory.core.entity_tree.model import EntityTree, TreeNode


def mark_dirty(node: TreeNode) -> None:
    """Set dirty=True on immediate node only. Phase 3 heap handles upward cascade."""
    node.dirty = True


def increment_uncertainty(node: TreeNode, amount: float = 1.0) -> None:
    """Bump uncertainty score on a non-leaf node (for audit_across scheduling)."""
    if node is not None and node.node_type != "leaf":
        node.uncertainty_score += amount


def _update_support_fresh_centroid(node: TreeNode, leaf_emb: np.ndarray | None) -> None:
    """Walk ancestors, incrementing support/fresh_count and updating centroid."""
    cur = node
    while cur is not None:
        cur.support += 1
        cur.fresh_count += 1
        if leaf_emb is not None:
            old_s = cur.support - 1  # support before this increment
            if cur.subtree_centroid is not None and old_s > 0:
                cur.subtree_centroid = (
                    cur.subtree_centroid * old_s + leaf_emb
                ) / cur.support
            else:
                cur.subtree_centroid = leaf_emb.copy()
        cur = cur.parent


def _get_child_descriptions(node: TreeNode) -> list[str]:
    """Collect text descriptions of a node's children for summarization."""
    descs: list[str] = []
    for child in node.children:
        if child._removed:
            continue
        if child.node_type == "leaf" and child.fact_text:
            descs.append(child.fact_text)
        elif child.description:
            descs.append(child.description)
    return descs


def D_max(n: int) -> int:
    """Maximum expected depth for a subtree with *n* leaves."""
    if n <= 0:
        return 0
    return round(settings.DEPTH_D0 + settings.DEPTH_C * math.log(n))


def S_floor(d: int) -> int:
    """Minimum support (leaf count) expected at depth *d*."""
    raw = math.exp((d - settings.DEPTH_D0) / settings.DEPTH_C)
    nearest = round(raw)
    # Snap to nearest integer when within tolerance (FP near-boundary),
    # otherwise ceil; always at least 1.
    if abs(raw - nearest) < 0.1:
        return max(1, nearest)
    return max(1, math.ceil(raw))


def _depth(node: TreeNode) -> int:
    """Walk parent chain to compute depth."""
    d = 0
    cur = node
    while cur.parent is not None:
        d += 1
        cur = cur.parent
    return d


def compute_debt(node: TreeNode) -> float:
    """Compute structural debt for audit scheduling."""
    base = node.uncertainty_score

    active_children = len([c for c in node.children if not c._removed])
    w_soft = settings.W_SOFT_BASE + settings.W_SOFT_LOG * math.log2(1 + node.support)
    width_p = max(0.0, active_children - w_soft)

    d = _depth(node)
    d_max = D_max(node.support) if node.support > 0 else 999
    depth_p = max(0.0, d - d_max)

    return base + settings.W_WIDTH * width_p + settings.W_DEPTH * depth_p


def _create_root(task_id: int, entity_id: str) -> EntityTree:
    """Create a new tree with just a root node."""
    root = TreeNode(node_type="root", dirty=True)
    return EntityTree(task_id, entity_id, root)


def _create_leaf(
    fact_id: int, fact_text: str, fact_embedding: np.ndarray | None
) -> TreeNode:
    """Create a leaf node for a fact."""
    return TreeNode(
        node_type="leaf",
        fact_id=fact_id,
        fact_text=fact_text,
        fact_embedding=fact_embedding,
        support=1,
        subtree_centroid=fact_embedding.copy() if fact_embedding is not None else None,
    )


def _attach_leaf(parent: TreeNode, leaf: TreeNode, tree: EntityTree) -> None:
    """Attach a leaf to a parent and update tree index."""
    leaf.parent = parent
    parent.children.append(leaf)
    if leaf.fact_id is not None:
        tree.leaf_index[leaf.fact_id] = leaf
    _update_support_fresh_centroid(parent, leaf.fact_embedding)
    mark_dirty(parent)
    increment_uncertainty(parent)


def attach_all(
    tree: EntityTree,
    routing_result: dict[int, list],
    target_nodes: dict[int, TreeNode],
) -> dict[int, list[np.ndarray]]:
    """Phase 1: attach routed facts as leaves. Returns trigger map for Phase 3."""
    triggers: dict[int, list[np.ndarray]] = {}
    for node_key, facts in routing_result.items():
        parent = target_nodes[node_key]
        embs: list[np.ndarray] = []
        for fact in facts:
            raw = fact.text_embedding
            if raw is not None and not isinstance(raw, np.ndarray):
                raw = np.array(raw.to_numpy() if hasattr(raw, "to_numpy") else raw)
            leaf = _create_leaf(fact.id, fact.text, raw)
            _attach_leaf(parent, leaf, tree)
            if raw is not None:
                embs.append(raw)
        if embs:
            triggers[id(parent)] = embs
    return triggers


def _create_aspect(
    description: str,
    desc_embedding: np.ndarray | None,
    parent: TreeNode,
) -> TreeNode:
    """Create an aspect node and attach to parent."""
    aspect = TreeNode(
        node_type="aspect",
        description=description,
        description_embedding=desc_embedding,
        dirty=False,
    )
    aspect.parent = parent
    parent.children.append(aspect)
    return aspect


def _tree_is_flat(tree: EntityTree) -> bool:
    """Check if tree is flat (root → leaves only, no aspect nodes)."""
    for child in tree.root.children:
        if child.node_type != "leaf":
            return False
    return True


def _remove_node(node: TreeNode, tree: EntityTree) -> None:
    """Mark a node (and its subtree) as removed in the in-memory tree."""
    node._removed = True
    if node.fact_id and node.fact_id in tree.leaf_index:
        del tree.leaf_index[node.fact_id]
    if node.parent and node in node.parent.children:
        node.parent.children.remove(node)
    for child in list(node.children):
        _remove_node(child, tree)


def _reparent(node: TreeNode, new_parent: TreeNode) -> None:
    """Move a node from its current parent to a new parent."""
    old_parent = node.parent
    moved_support = node.support

    if old_parent and node in old_parent.children:
        old_parent.children.remove(node)
    increment_uncertainty(old_parent)

    # Subtract support from old ancestors, mark centroid stale
    if old_parent is not None:
        old_parent._centroid_stale = True
        cur = old_parent
        while cur is not None:
            cur.support -= moved_support
            cur = cur.parent

    node.parent = new_parent
    new_parent.children.append(node)
    increment_uncertainty(new_parent)

    # Add support to new ancestors, mark centroid stale
    new_parent._centroid_stale = True
    cur = new_parent
    while cur is not None:
        cur.support += moved_support
        cur = cur.parent


def _collapse_single_child_aspects(node: TreeNode, tree: EntityTree) -> None:
    """Remove redundant intermediate aspect nodes with exactly one non-leaf child."""
    for child in list(node.children):
        if not child._removed:
            _collapse_single_child_aspects(child, tree)

    active_children = [c for c in node.children if not c._removed]
    if len(active_children) != 1:
        return
    only_child = active_children[0]
    if only_child.node_type == "leaf":
        return

    if node.node_type == "root":
        for gc in list(only_child.children):
            if not gc._removed:
                _reparent(gc, node)
        _remove_node(only_child, tree)
        mark_dirty(node)
    elif node.node_type == "aspect" and node.parent:
        parent = node.parent
        _reparent(only_child, parent)
        _remove_node(node, tree)
        mark_dirty(parent)


def _count_dirty(node: TreeNode) -> int:
    """Count dirty non-leaf nodes in a subtree."""
    count = 1 if (node.dirty and node.node_type != "leaf") else 0
    for child in node.children:
        if not child._removed:
            count += _count_dirty(child)
    return count


def dissolve_node(node: TreeNode, tree: EntityTree) -> None:
    """Dissolve node: reparent children to parent, remove node."""
    parent = node.parent
    assert parent is not None
    for child in list(node.children):
        if not child._removed:
            _reparent(child, parent)
    _remove_node(node, tree)
    mark_dirty(parent)


def auto_dissolve(tree: EntityTree) -> int:
    """Dissolve all aspect nodes with ≤ 1 active children.
    Pure code, no LLM. Loops until stable. Never touches root."""
    total = 0
    while True:
        targets = _find_dissolvable_aspects(tree.root)
        if not targets:
            break
        for node in targets:
            if not node._removed:
                dissolve_node(node, tree)
                total += 1
    return total


def _find_dissolvable_aspects(root: TreeNode) -> list[TreeNode]:
    """BFS scan for dissolvable aspects:
    1. Original: aspect with <= 1 active child.
    2. New: thin chain — node below S_floor AND parent also below S_floor.
    """
    result: list[TreeNode] = []
    queue = [root]
    while queue:
        n = queue.pop(0)
        if n._removed or n.node_type == "leaf":
            continue
        if n.node_type == "aspect":
            active = [c for c in n.children if not c._removed]
            if len(active) <= 1:
                result.append(n)
            elif (
                n.parent is not None
                and n.parent.node_type != "root"
                and n.support < S_floor(_depth(n))
                and n.parent.support < S_floor(_depth(n.parent))
            ):
                result.append(n)
        for child in n.children:
            if not child._removed and child.node_type != "leaf":
                queue.append(child)
    return result


def _collect_direct_child_embeddings(node: TreeNode) -> list[np.ndarray]:
    """Collect embeddings from direct children (leaf→fact_emb, aspect→desc_emb)."""
    result: list[np.ndarray] = []
    for child in node.children:
        if child._removed:
            continue
        if child.node_type == "leaf" and child.fact_embedding is not None:
            result.append(child.fact_embedding)
        elif child.description_embedding is not None:
            result.append(child.description_embedding)
    return result
