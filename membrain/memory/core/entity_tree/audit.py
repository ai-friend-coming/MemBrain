"""Phase 2: Unified budgeted structural audit (audit_down + audit_across + force_split).

Per-batch: scan all non-leaf nodes, filter by uncertainty threshold, run audit_down
and audit_across on the top-K nodes, then force_split and dissolve.
"""

from __future__ import annotations

import json
import logging
import math
from typing import TYPE_CHECKING

import numpy as np

from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.agents.retry import run_agent_with_retry
from membrain.config import settings
from membrain.memory.core.entity_tree.model import EntityTree, TreeNode
from membrain.memory.core.entity_tree.schemas import (
    register_audit_across_validator,
    register_audit_down_validator,
)
from membrain.memory.core.entity_tree.text import render_fact_text
from membrain.memory.core.entity_tree.tree_ops import (
    _create_aspect,
    _remove_node,
    _reparent,
    auto_dissolve,
    compute_debt,
    mark_dirty,
)

if TYPE_CHECKING:
    from membrain.infra.clients.embedding import EmbeddingClient

log = logging.getLogger(__name__)


# ── audit_down ────────────────────────────────────────────────────────────────


async def audit_down(
    node: TreeNode,
    tree: EntityTree,
    embed_client: EmbeddingClient,
    registry: TaskRegistry,
    factory: AgentFactory,
    split_mode: bool = False,
) -> int:
    """Audit children organisation within *node*.

    Executes GROUP actions in-place. Returns number of GROUPs executed.
    """
    children = [c for c in node.children if not c._removed]
    if len(children) < 2:
        return 0

    # Warm-up skip: root node whose children are all leaves and still small.
    # Only fires in the earliest batches; once an aspect exists this is bypassed.
    if (
        node.node_type == "root"
        and len(children) <= settings.AUDIT_DOWN_WARMUP_MIN
        and all(c.node_type == "leaf" for c in children)
    ):
        return 0

    children_info = []
    cid_map: dict[str, TreeNode] = {}
    for i, child in enumerate(children):
        cid = f"c{i}"
        cid_map[cid] = child
        if child.node_type == "leaf":
            children_info.append(
                {
                    "id": cid,
                    "type": "fact",
                    "text": render_fact_text(child.fact_text or ""),
                }
            )
        else:
            child_count = len([gc for gc in child.children if not gc._removed])
            children_info.append(
                {
                    "id": cid,
                    "type": "aspect",
                    "description": child.description or "",
                    "child_count": child_count,
                }
            )

    agent, agent_settings = factory.get_agent("tree-audit-down")
    register_audit_down_validator(agent)
    prompts = registry.render_prompts(
        "tree-audit-down",
        node_description=node.description or "(root)",
        children_json=json.dumps(children_info, ensure_ascii=False),
        split_mode="true" if split_mode else "false",
    )
    deps = {"child_ids": list(cid_map.keys())}
    try:
        result = await run_agent_with_retry(
            agent, instructions=prompts, model_settings=agent_settings, deps=deps
        )
        actions = [a.model_dump() for a in result.output.actions]
    except Exception:
        log.warning("audit_down failed, skipping")
        return 0

    groups_executed = 0
    for action in actions:
        if _execute_down_action(action, cid_map, node, tree, embed_client):
            groups_executed += 1
    return groups_executed


def _execute_down_action(
    action: dict,
    cid_map: dict[str, TreeNode],
    parent: TreeNode,
    tree: EntityTree,
    embed_client: EmbeddingClient,
) -> bool:
    """Execute a single audit_down action. Returns True if GROUP was executed."""
    act = action["action"]
    tids = action.get("target_ids", [])

    if act != "GROUP" or len(tids) < 2:
        return False

    label = action.get("label") or "group"
    emb = None
    try:
        emb = np.array(embed_client.embed_single(label))
    except Exception:
        pass

    # Count effective children to decide merge vs wrapper mode.
    # leaf → 1 (the leaf itself), aspect → len(active children)
    targets = [
        cid_map[tid] for tid in tids if cid_map.get(tid) and not cid_map[tid]._removed
    ]
    total_effective = 0
    for t in targets:
        if t.node_type == "leaf":
            total_effective += 1
        else:
            total_effective += len([c for c in t.children if not c._removed])

    new_aspect = _create_aspect(label, emb, parent)

    if total_effective < settings.TREE_MERGE_THRESHOLD:
        # MERGE mode: flatten targets' children into new_aspect
        for t in targets:
            if t.node_type == "aspect":
                for gc in list(t.children):
                    if not gc._removed:
                        _reparent(gc, new_aspect)
                _remove_node(t, tree)
            else:
                _reparent(t, new_aspect)
        log.info(
            "      audit_down GROUP-MERGE [%s] → '%s' (%d effective children)",
            ", ".join(tids),
            label,
            total_effective,
        )
    else:
        # WRAPPER mode: reparent targets as-is (original behavior)
        for t in targets:
            _reparent(t, new_aspect)
        log.info(
            "      audit_down GROUP-WRAP [%s] → '%s' (%d effective children)",
            ", ".join(tids),
            label,
            total_effective,
        )

    mark_dirty(new_aspect)
    return True


# ── audit_across ──────────────────────────────────────────────────────────────


async def audit_across(
    target_node: TreeNode,
    tree: EntityTree,
    embed_client: EmbeddingClient,
    registry: TaskRegistry,
    factory: AgentFactory,
) -> list[dict]:
    """Audit ALL children of *target_node* for cross-boundary relocation.

    Examines every child and the node's siblings as candidate destinations.
    Returns executed actions.
    """
    parent = target_node.parent
    if parent is None:
        # target_node is root — no parent to PROMOTE to, no siblings to RELOCATE into.
        return []

    # Build children_info with c0, c1, ... IDs (ALL active children)
    children = [c for c in target_node.children if not c._removed]
    if len(children) < 2:
        return []

    children_info = []
    cid_map: dict[str, TreeNode] = {}
    for i, child in enumerate(children):
        cid = f"c{i}"
        cid_map[cid] = child
        if child.node_type == "leaf":
            children_info.append(
                {
                    "id": cid,
                    "type": "fact",
                    "text": render_fact_text(child.fact_text or ""),
                }
            )
        else:
            child_count = len([gc for gc in child.children if not gc._removed])
            children_info.append(
                {
                    "id": cid,
                    "type": "aspect",
                    "description": child.description or "",
                    "child_count": child_count,
                }
            )

    # Build siblings_info: parent's other aspect children as relocation targets
    siblings_info = []
    sid_map: dict[str, TreeNode] = {}
    s_counter = 0
    for sibling in parent.children:
        if sibling._removed or sibling is target_node:
            continue
        if sibling.node_type == "leaf":
            continue
        sid = f"s{s_counter}"
        s_counter += 1
        sid_map[sid] = sibling
        child_count = len([gc for gc in sibling.children if not gc._removed])
        siblings_info.append(
            {
                "id": sid,
                "type": "aspect",
                "description": sibling.description or "",
                "child_count": child_count,
            }
        )

    if not siblings_info:
        return []

    agent, agent_settings = factory.get_agent("tree-audit-across")
    register_audit_across_validator(agent)
    prompts = registry.render_prompts(
        "tree-audit-across",
        node_description=target_node.description or "(root)",
        children_json=json.dumps(children_info, ensure_ascii=False),
        parent_description=parent.description or "(root)",
        siblings_json=json.dumps(siblings_info, ensure_ascii=False),
    )
    deps = {
        "child_ids": list(cid_map.keys()),
        "sibling_ids": list(sid_map.keys()),
    }
    try:
        result = await run_agent_with_retry(
            agent, instructions=prompts, model_settings=agent_settings, deps=deps
        )
        actions = [a.model_dump() for a in result.output.actions]
    except Exception:
        log.warning("audit_across failed, skipping")
        return []

    for action in actions:
        _execute_across_action(action, cid_map, sid_map, parent, target_node)
    return actions


def _execute_across_action(
    action: dict,
    cid_map: dict[str, TreeNode],
    sid_map: dict[str, TreeNode],
    parent: TreeNode,
    target_node: TreeNode,
) -> None:
    """Execute a single audit_across action."""
    act = action["action"]
    target_id = action.get("target_id", "")
    destination_id = action.get("destination_id") or ""

    child = cid_map.get(target_id)
    if child is None or child._removed:
        return

    if act == "PROMOTE":
        _reparent(child, parent)
        mark_dirty(parent)
        mark_dirty(target_node)
        child_desc = (child.fact_text or child.description or "?")[:60]
        log.info("      audit_across PROMOTE [%s]: %s", target_id, child_desc)

    elif act == "RELOCATE":
        dest = sid_map.get(destination_id)
        if dest and not dest._removed:
            _reparent(child, dest)
            mark_dirty(dest)
            mark_dirty(target_node)
            child_desc = (child.fact_text or child.description or "?")[:50]
            dest_desc = (dest.description or "?")[:50]
            log.info(
                "      audit_across RELOCATE [%s]: %s → '%s'",
                target_id,
                child_desc,
                dest_desc,
            )


# ── force_split ───────────────────────────────────────────────────────────────


def _find_overflow_nodes(node: TreeNode) -> list[TreeNode]:
    """BFS: return all nodes whose active child count exceeds MAX_CHILDREN."""
    result: list[TreeNode] = []
    queue = [node]
    while queue:
        n = queue.pop(0)
        active = [c for c in n.children if not c._removed]
        if len(active) > settings.TREE_MAX_CHILDREN:
            result.append(n)
        for child in active:
            if child.node_type != "leaf":
                queue.append(child)
    return result


def _fallback_force_split(
    node: TreeNode,
    embed_client: EmbeddingClient,
) -> None:
    """Equal-partition fallback: split children into ceil(N/MAX) groups."""
    children = [c for c in node.children if not c._removed]
    n = len(children)
    max_ch = settings.TREE_MAX_CHILDREN
    n_groups = math.ceil(n / max_ch)
    group_size = math.ceil(n / n_groups)
    for g_idx in range(n_groups):
        label = f"group-{g_idx + 1}"
        emb = None
        try:
            emb = np.array(embed_client.embed_single(label))
        except Exception:
            pass
        new_aspect = _create_aspect(label, emb, node)
        batch = children[g_idx * group_size : (g_idx + 1) * group_size]
        for child in batch:
            if not child._removed:
                _reparent(child, new_aspect)
        mark_dirty(new_aspect)


async def force_split(
    tree: EntityTree,
    embed_client: EmbeddingClient,
    registry: TaskRegistry,
    factory: AgentFactory,
) -> None:
    """Step 3: scan for overflow nodes and split them.

    Tries audit_down(split_mode=True) first; falls back to equal-partition
    if the LLM call fails or the node is still overflowing afterwards.
    """
    overflow = _find_overflow_nodes(tree.root)
    for node in overflow:
        if node._removed:
            continue
        active_before = [c for c in node.children if not c._removed]
        if len(active_before) <= settings.TREE_MAX_CHILDREN:
            continue

        await audit_down(node, tree, embed_client, registry, factory, split_mode=True)

        # Check if still overflowing
        active_after = [c for c in node.children if not c._removed]
        if len(active_after) > settings.TREE_MAX_CHILDREN:
            log.warning("force_split LLM insufficient, using fallback")
            _fallback_force_split(node, embed_client)


# ── run_budgeted_audit ────────────────────────────────────────────────────────


def _collect_non_leaf_nodes(root: TreeNode) -> list[TreeNode]:
    """BFS: collect all non-leaf, non-removed nodes in the tree."""
    result: list[TreeNode] = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        if node._removed:
            continue
        if node.node_type != "leaf":
            result.append(node)
        for child in node.children:
            if not child._removed:
                queue.append(child)
    return result


async def run_budgeted_audit(
    tree: EntityTree,
    embed_client: EmbeddingClient,
    registry: TaskRegistry,
    factory: AgentFactory,
) -> None:
    """Phase 2: unified audit pipeline.

    1. Scan all non-leaf nodes, filter by uncertainty threshold, take top-K.
    2. For each: audit_down (GROUP) + audit_across (PROMOTE/RELOCATE), then reset.
    3. force_split → auto_dissolve.
    """
    # Step 1: select candidates by debt score
    all_nodes = _collect_non_leaf_nodes(tree.root)
    candidates = [
        n for n in all_nodes if compute_debt(n) >= settings.AUDIT_MIN_UNCERTAINTY
    ]
    candidates.sort(key=lambda n: compute_debt(n), reverse=True)

    # Step 2: audit top-K
    for node in candidates[: settings.AUDIT_MAX_K]:
        if node._removed:
            continue
        await audit_down(node, tree, embed_client, registry, factory)
        await audit_across(node, tree, embed_client, registry, factory)
        node.uncertainty_score = 0.0

    # Step 3: Force Split
    await force_split(tree, embed_client, registry, factory)

    # Step 4: Auto Dissolve
    dissolved = auto_dissolve(tree)
    if dissolved:
        log.info(
            "    [audit:%s] auto_dissolve: %d aspect(s) dissolved",
            tree.entity_id,
            dissolved,
        )
