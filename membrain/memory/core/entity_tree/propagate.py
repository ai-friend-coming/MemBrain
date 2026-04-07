"""Phase 3+4: Reachability-gated description propagation + batch embed.

Bottom-up heap-driven description rebuild with reachability gate, then batch re-embed stale nodes.
"""

from __future__ import annotations

import heapq
import json
import logging

import numpy as np

from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.agents.retry import run_agent_with_retry
from membrain.config import settings
from membrain.memory.core.entity_tree.model import EntityTree, TreeNode
from membrain.memory.core.entity_tree.similarity import cosine_similarity
from membrain.memory.core.entity_tree.text import render_fact_text
from membrain.memory.core.entity_tree.tree_ops import (
    _collect_direct_child_embeddings,
    _get_child_descriptions,
)

log = logging.getLogger(__name__)


async def propagate_with_reachability_gate(
    tree: EntityTree,
    phase1_nodes: set[int],
    registry: TaskRegistry,
    factory: AgentFactory,
) -> None:
    """Phase 3: bottom-up description propagation with centroid-based post-update gate."""
    audit_dirty = _collect_dirty_non_leaf(tree.root)

    heap: list[tuple[int, int, TreeNode]] = []
    seen_ids: set[int] = set()

    for nid in phase1_nodes:
        node = _resolve_node_by_id(tree, nid)
        if node is not None and node.node_type != "leaf":
            heap.append((-_depth(node), nid, node))
            seen_ids.add(nid)

    for node in audit_dirty:
        nid = id(node)
        if nid not in seen_ids:
            heap.append((-_depth(node), nid, node))
            seen_ids.add(nid)

    heapq.heapify(heap)
    processed: set[int] = set()

    while heap:
        _, nid, node = heapq.heappop(heap)
        if nid in processed or node._removed:
            continue
        processed.add(nid)

        descs = _get_child_descriptions(node)
        if not descs:
            continue
        rendered = [render_fact_text(d) for d in descs]
        concatenated = " | ".join(rendered)

        try:
            summary = await _llm_summarize(concatenated, registry, factory)
            node.description = summary
        except Exception:
            log.warning("Summarize failed, keeping previous description")
            continue

        node._embedding_stale = True
        node.dirty = False
        node.fresh_count = 0  # reset after refresh

        # Post-update gate: use centroid escape + min_fresh for upward propagation.
        # was_dirty controls whether THIS node was refreshed unconditionally;
        # upward propagation is always gated the same way.
        if node.parent is not None and _should_propagate_up(node):
            pid = id(node.parent)
            if pid not in seen_ids:
                heapq.heappush(heap, (-_depth(node.parent), pid, node.parent))
                seen_ids.add(pid)


def _centroid_escaped(node: TreeNode) -> bool:
    """Return True if children centroid routes to a sibling — parent should update.

    Uses node's current (possibly stale) description_embedding as its representative,
    so the check asks: "from the parent's current view, has my content drifted away?"
    """
    parent = node.parent
    if parent is None:
        return True
    siblings = [c for c in parent.children if not c._removed and c.node_type != "leaf"]
    if len(siblings) <= 1:
        return False

    child_embs = _collect_direct_child_embeddings(node)
    if not child_embs:
        return False

    centroid = np.mean(child_embs, axis=0)
    best_sim = -1.0
    best = None
    for sib in siblings:
        emb = sib.description_embedding
        if emb is None:
            continue
        sim = cosine_similarity(centroid, emb)
        if sim > best_sim:
            best_sim = sim
            best = sib

    return best is not node


def _should_propagate_up(node: TreeNode) -> bool:
    """Decide whether to propagate description refresh to node's parent.

    Requires BOTH:
    1. parent.fresh_count >= MIN_FRESH_FOR_PROPAGATE
    2. centroid escaped (semantic drift detected)
    """
    parent = node.parent
    if parent is None:
        return False
    if parent.fresh_count < settings.MIN_FRESH_FOR_PROPAGATE:
        return False
    return _centroid_escaped(node)


def batch_embed_descriptions(tree: EntityTree, embed_client) -> None:
    """Phase 4: batch-compute embeddings for all nodes with stale descriptions."""
    nodes = _collect_embedding_stale_nodes(tree.root)
    if not nodes:
        return
    texts = [n.description for n in nodes]
    embeddings = embed_client.embed(texts)  # EmbeddingClient.embed() is already batch
    for node, emb in zip(nodes, embeddings):
        node.description_embedding = np.array(emb)
        node._embedding_stale = False


def batch_recompute_centroids(tree: EntityTree) -> None:
    """Recompute subtree_centroid for all nodes marked _centroid_stale.

    Bottom-up traversal: leaves first, then aspects, then root.
    """
    all_nodes: list[tuple[int, TreeNode]] = []
    queue = [tree.root]
    while queue:
        node = queue.pop(0)
        if node._removed:
            continue
        all_nodes.append((_depth(node), node))
        for child in node.children:
            if not child._removed:
                queue.append(child)

    # Sort deepest first
    all_nodes.sort(key=lambda x: -x[0])

    for _, node in all_nodes:
        if node.node_type == "leaf":
            node.subtree_centroid = (
                node.fact_embedding.copy() if node.fact_embedding is not None else None
            )
            node._centroid_stale = False
            continue

        if not node._centroid_stale:
            continue

        # Recompute from children
        total_support = 0
        weighted_sum = None
        for child in node.children:
            if child._removed or child.subtree_centroid is None:
                continue
            s = child.support if child.support > 0 else 1
            if weighted_sum is None:
                weighted_sum = child.subtree_centroid * s
            else:
                weighted_sum = weighted_sum + child.subtree_centroid * s
            total_support += s

        if weighted_sum is not None and total_support > 0:
            node.subtree_centroid = weighted_sum / total_support
        else:
            node.subtree_centroid = None

        node._centroid_stale = False


def _collect_dirty_non_leaf(root: TreeNode) -> list[TreeNode]:
    """Collect non-leaf nodes marked dirty (from audit operations)."""
    result: list[TreeNode] = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        if node._removed:
            continue
        if node.dirty and node.node_type != "leaf":
            result.append(node)
        for child in node.children:
            if not child._removed:
                queue.append(child)
    return result


def _collect_embedding_stale_nodes(root: TreeNode) -> list[TreeNode]:
    """Collect all nodes with _embedding_stale=True."""
    result: list[TreeNode] = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        if node._removed:
            continue
        if node._embedding_stale and node.description:
            result.append(node)
        for child in node.children:
            if not child._removed:
                queue.append(child)
    return result


def _resolve_node_by_id(tree: EntityTree, nid: int) -> TreeNode | None:
    """Find a node in the tree by its Python id(). Walks the tree."""
    queue = [tree.root]
    while queue:
        node = queue.pop(0)
        if id(node) == nid:
            return node
        for child in node.children:
            if not child._removed:
                queue.append(child)
    return None


def _depth(node: TreeNode) -> int:
    d = 0
    cur = node
    while cur.parent is not None:
        d += 1
        cur = cur.parent
    return d


def _token_estimate(text: str) -> int:
    return len(text.split())


async def _llm_summarize(
    text: str,
    registry: TaskRegistry,
    factory: AgentFactory,
) -> str:
    """Call tree-summarizer to compress long concatenated text."""
    agent, agent_settings = factory.get_agent("tree-summarizer")
    prompts = registry.render_prompts(
        "tree-summarizer",
        entity_ref="(node)",
        children_descriptions_json=json.dumps([text], ensure_ascii=False),
    )
    result = await run_agent_with_retry(
        agent,
        instructions=prompts,
        model_settings=agent_settings,
    )
    return result.output
