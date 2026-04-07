"""Aspect enrichment: build tree paths for facts and format for reranking."""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from membrain.config import settings
from membrain.retrieval.core.types import AspectInfo

__all__ = ["AspectInfo"]

log = logging.getLogger(__name__)


def build_aspect_paths(
    fact_ids: list[int],
    task_id: int,
    db: Session,
) -> dict[int, AspectInfo]:
    """Look up entity tree ancestry for each fact.

    Joins leaf nodes up through parent chain (max 3 levels: leaf → aspect → root)
    and fetches entity canonical_ref.

    Returns dict mapping fact_id → AspectInfo. Facts not in any tree are omitted.
    """
    if not fact_ids:
        return {}

    sql = sa_text("""
        SELECT
            leaf.fact_id,
            e.canonical_ref,
            e.desc,
            parent.node_type  AS parent_type,
            parent.description AS parent_desc,
            gp.node_type       AS gp_type,
            gp.description     AS gp_desc
        FROM entity_tree_nodes leaf
        JOIN entity_tree_nodes parent ON parent.id = leaf.parent_id
        LEFT JOIN entity_tree_nodes gp ON gp.id = parent.parent_id
        JOIN entities e
          ON e.entity_id = leaf.entity_id
         AND e.task_id = :task_id
        WHERE leaf.fact_id = ANY(:fact_ids)
          AND leaf.task_id = :task_id
          AND leaf.node_type = 'leaf'
    """)
    rows = db.execute(sql, {"fact_ids": fact_ids, "task_id": task_id}).fetchall()

    result: dict[int, AspectInfo] = {}
    for row in rows:
        fid = row[0]
        entity_ref = row[1] or ""
        entity_desc = row[2] or ""
        parent_type = row[3]
        parent_desc = row[4] or ""
        gp_type = row[5]
        gp_desc = row[6] or ""

        if parent_type == "root":
            # 2-level tree: root → leaf (no intermediate aspect)
            path = parent_desc
            leaf_desc = parent_desc
            mid_desc = ""
        elif gp_type == "root":
            # 3-level tree: root → aspect(parent) → leaf
            path = f"{gp_desc} > {parent_desc}" if gp_desc else parent_desc
            leaf_desc = parent_desc
            mid_desc = gp_desc
        else:
            # deeper tree or missing gp — use parent as leaf
            path = parent_desc
            leaf_desc = parent_desc
            mid_desc = gp_desc

        leaf_key = f"{entity_ref}::{leaf_desc}"
        mid_key = f"{entity_ref}::{mid_desc}" if mid_desc else leaf_key

        result[fid] = AspectInfo(
            entity_ref=entity_ref,
            entity_desc=entity_desc,
            leaf_desc=leaf_desc,
            mid_desc=mid_desc,
            path=path,
            leaf_key=leaf_key,
            mid_key=mid_key,
        )
    return result


def enrich_for_rerank(
    fact_text: str,
    info: AspectInfo | None,
    time_info: str = "",
) -> str:
    """Build enriched passage for reranker input.

    Format: [Entity > Aspect path: leaf summary] fact_text (date: time)
    Falls back to bare fact_text if no AspectInfo.
    """
    if info is None:
        return f"{fact_text} (date: {time_info})" if time_info else fact_text

    entity_label = (
        f"{info.entity_ref} ({info.entity_desc})"
        if info.entity_desc
        else info.entity_ref
    )
    header = entity_label
    if info.path:
        header = f"{header} > {info.path}"
    summary_part = f": {info.leaf_desc}" if info.leaf_desc else ""
    enriched = f"[{header}{summary_part}] {fact_text}"
    if time_info:
        enriched += f" (date: {time_info})"
    return enriched


def aspect_dedup(
    fact_ids_ordered: list[int],
    aspect_infos: dict[int, AspectInfo],
    max_per_leaf: int = settings.QA_MAX_PER_LEAF_ASPECT,
    max_per_mid: int = settings.QA_MAX_PER_MID_ASPECT,
    protected_ids: set[int] | None = None,
) -> list[int]:
    """Filter fact IDs by aspect-level caps, preserving input order.

    Facts without AspectInfo always pass (no tree info to dedup on).
    Facts in protected_ids bypass the leaf/mid caps (BM25 keyword hits).
    """
    leaf_counts: dict[str, int] = defaultdict(int)
    mid_counts: dict[str, int] = defaultdict(int)
    kept: list[int] = []

    for fid in fact_ids_ordered:
        info = aspect_infos.get(fid)
        if info is None:
            kept.append(fid)
            continue
        if protected_ids and fid in protected_ids:
            kept.append(fid)
            continue
        if leaf_counts[info.leaf_key] >= max_per_leaf:
            continue
        if mid_counts[info.mid_key] >= max_per_mid:
            continue
        leaf_counts[info.leaf_key] += 1
        mid_counts[info.mid_key] += 1
        kept.append(fid)

    return kept
