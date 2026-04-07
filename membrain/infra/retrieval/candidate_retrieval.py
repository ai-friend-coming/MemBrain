"""Hybrid BM25 + embedding candidate retrieval for entity resolution."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from membrain.config import settings
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.models.memory import EntityModel, FactRefModel

log = logging.getLogger(__name__)


@dataclass
class CandidateEntry:
    """Single (name, entity_id) pair for Layer 1/2 deterministic resolution."""

    entity_id: str
    name: str
    shingles: set[str]


_TANTIVY_SPECIAL = re.compile(r"""[+\-&|!(){}\[\]^"~*?:\\/'.,$;`]""")


def _sanitize_bm25_query(raw: str) -> str:
    """Escape special chars for ParadeDB / Tantivy.

    Drops non-ASCII characters first (Unicode dashes, curly quotes, em-dashes, etc.)
    so that Tantivy's query parser never sees characters it can't handle.
    """
    ascii_only = raw.encode("ascii", errors="ignore").decode("ascii")
    ascii_only = ascii_only.replace("\x00", " ")  # NUL bytes break psycopg2
    cleaned = _TANTIVY_SPECIAL.sub(" ", ascii_only)
    return " ".join(cleaned.split())


def _bm25_search(
    query: str,
    task_id: int,
    db: Session,
    limit: int = 30,
) -> list[tuple[str, str]]:
    """Return (alias_text, entity_id) rows matching query. No dedup by entity."""
    safe_query = _sanitize_bm25_query(query)
    if not safe_query:
        return []
    sql = sa_text("""
        SELECT fr.alias_text, e.entity_id
        FROM fact_refs fr
        JOIN entities e ON e.entity_id = fr.entity_id AND e.task_id = :task_id
        WHERE fr.id @@@ paradedb.parse(:query)
        ORDER BY paradedb.score(fr.id) DESC
        LIMIT :limit
    """)
    try:
        with db.begin_nested():
            rows = db.execute(
                sql, {"query": safe_query, "task_id": task_id, "limit": limit}
            ).fetchall()
    except Exception:
        log.warning("BM25 fact_refs search failed for query %r", query, exc_info=True)
        return []
    return [(r[0], r[1]) for r in rows]


def _embedding_search(
    query_vec: list[float],
    task_id: int,
    db: Session,
    limit: int = 20,
) -> list[tuple[str, str, str, float]]:
    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
    sql = sa_text("""
        SELECT entity_id, canonical_ref, "desc",
               -(desc_embedding <#> CAST(:vec AS halfvec)) AS score
        FROM entities
        WHERE task_id = :task_id
          AND desc_embedding IS NOT NULL
        ORDER BY desc_embedding <#> CAST(:vec AS halfvec)
        LIMIT :limit
    """)
    rows = db.execute(
        sql, {"vec": vec_str, "task_id": task_id, "limit": limit}
    ).fetchall()
    return rows


def _fetch_aliases_by_entity(
    db: Session,
    entity_ids: list[str] | set[str],
) -> dict[str, list[str]]:
    """Fetch distinct alias texts per entity from fact_refs."""
    if not entity_ids:
        return {}
    rows = (
        db.query(FactRefModel.entity_id, FactRefModel.alias_text)
        .filter(FactRefModel.entity_id.in_(list(entity_ids)))
        .distinct()
        .all()
    )
    result: dict[str, list[str]] = {}
    for entity_id, alias_text in rows:
        result.setdefault(entity_id, []).append(alias_text)
    return result


@dataclass
class EntityContext:
    """An entity from the DB included in the extraction context."""

    entity_id: str
    canonical_ref: str
    aliases: list[str]
    desc: str


def retrieve_candidate_pool(
    entity_names: list[str],
    task_id: int,
    db: Session,
    embed_client: EmbeddingClient,
    top_k: int | None = None,
) -> tuple[list[CandidateEntry], dict[str, Any], dict[str, list[str]]]:
    """Retrieve n+1 CandidateEntry list for resolver Layers 1/2/3.

    Returns:
        entries: flat list of (name, entity_id) CandidateEntry, one per alias + canonical_ref
        by_entity_id: EntityModel keyed by entity_id
        aliases_by_entity: alias lists keyed by entity_id
    """
    from membrain.memory.core.entity_resolver import _normalize_fuzzy, _shingles

    if top_k is None:
        top_k = settings.RESOLVER_CANDIDATE_TOP_K

    has_entities = (
        db.query(EntityModel.id)
        .filter(EntityModel.task_id == task_id)
        .limit(1)
        .scalar()
        is not None
    )
    if not has_entities:
        return [], {}, {}

    # ── BM25: alias-level hits ──
    alias_pairs: list[tuple[str, str]] = []  # (alias_text, entity_id)
    for name in entity_names:
        alias_pairs.extend(_bm25_search(name, task_id, db, limit=top_k * 3))

    # ── Embedding: entity-level hits ──
    candidate_eids_from_embed: set[str] = set()
    try:
        vecs = embed_client.embed(entity_names)
        for vec in vecs:
            embed_rows = _embedding_search(vec, task_id, db, limit=top_k)
            for row in embed_rows:
                candidate_eids_from_embed.add(row[0])
    except Exception:
        log.warning(
            "Embedding search failed for candidate pool, BM25-only", exc_info=True
        )

    # ── Combine entity_ids ──
    all_eids: set[str] = {eid for _, eid in alias_pairs} | candidate_eids_from_embed

    if not all_eids:
        return [], {}, {}

    # ── Load EntityModels ──
    from membrain.infra.queries import entities as entity_queries

    by_entity_id = entity_queries.find_merge_targets(db, task_id, list(all_eids))

    # ── Load aliases ──
    aliases_by_entity = _fetch_aliases_by_entity(db, all_eids)

    # ── Build n+1 CandidateEntry per entity ──
    entries: list[CandidateEntry] = []
    seen: set[tuple[str, str]] = set()

    for eid, ent_model in by_entity_id.items():
        names_for_entity = [ent_model.canonical_ref] + aliases_by_entity.get(eid, [])
        for name in names_for_entity:
            key = (name, eid)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                CandidateEntry(
                    entity_id=eid,
                    name=name,
                    shingles=_shingles(_normalize_fuzzy(name)),
                )
            )

    return entries, by_entity_id, aliases_by_entity
