"""Database-backed adapters for the memory ingestion pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from membrain.config import settings
from membrain.infra.persistence.batch_writer import write_batch_results
from membrain.infra.queries import entities as entity_queries
from membrain.infra.retrieval.candidate_retrieval import (
    EntityContext,
    _bm25_search,
    _embedding_search,
    _fetch_aliases_by_entity,
)
from membrain.infra.transaction_manager import TransactionManager
from membrain.memory.core.entity_resolver import resolve_entities

log = logging.getLogger(__name__)


def _interleave_candidates(
    per_query_eids: list[list[str]],
    top_k: int,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    cursors = [0] * len(per_query_eids)
    while len(selected) < top_k:
        advanced = False
        for i, ranked_list in enumerate(per_query_eids):
            while cursors[i] < len(ranked_list) and ranked_list[cursors[i]] in seen:
                cursors[i] += 1
            if cursors[i] < len(ranked_list):
                eid = ranked_list[cursors[i]]
                cursors[i] += 1
                seen.add(eid)
                selected.append(eid)
                advanced = True
                if len(selected) >= top_k:
                    return selected
        if not advanced:
            break
    return selected


def _retrieve_entity_context_for_extraction(
    entity_names: list[str],
    task_id: int,
    db,
    embed_client,
    top_k: int | None = None,
    per_query_limit: int | None = None,
) -> list[EntityContext]:
    if top_k is None:
        top_k = settings.EXTRACTION_CONTEXT_TOP_K
    if per_query_limit is None:
        per_query_limit = settings.EXTRACTION_CONTEXT_PER_QUERY

    if not entity_names:
        return []

    per_query_eids: list[list[str]] = []
    for name in entity_names:
        hits = _bm25_search(name, task_id, db, limit=per_query_limit * 3)
        seen: set[str] = set()
        eids: list[str] = []
        for _, eid in hits:
            if eid not in seen:
                seen.add(eid)
                eids.append(eid)
        per_query_eids.append(eids[:per_query_limit])

    try:
        vecs = embed_client.embed(entity_names)
    except Exception:
        log.warning("Embedding failed for extraction context, BM25-only", exc_info=True)
        vecs = []

    for i, vec in enumerate(vecs):
        rows = _embedding_search(vec, task_id, db, limit=per_query_limit)
        embed_eids = [row[0] for row in rows]
        if i < len(per_query_eids):
            existing = set(per_query_eids[i])
            for eid in embed_eids:
                if eid not in existing:
                    existing.add(eid)
                    per_query_eids[i].append(eid)
            per_query_eids[i] = per_query_eids[i][:per_query_limit]
        else:
            per_query_eids.append(embed_eids[:per_query_limit])

    selected_eids = _interleave_candidates(per_query_eids, top_k)
    if not selected_eids:
        return []

    by_eid = entity_queries.find_merge_targets(db, task_id, selected_eids)
    aliases_map = _fetch_aliases_by_entity(db, set(selected_eids))

    return [
        EntityContext(
            entity_id=eid,
            canonical_ref=by_eid[eid].canonical_ref,
            aliases=[
                alias
                for alias in aliases_map.get(eid, [])
                if alias != by_eid[eid].canonical_ref
            ],
            desc=by_eid[eid].desc or "",
        )
        for eid in selected_eids
        if eid in by_eid
    ]


@dataclass
class ResolvedDecisions:
    decisions: list[dict]
    canonicalizer_candidates: list[dict]


class MemoryIngestStore:
    """Infra adapter used by the application ingestion workflow."""

    def __init__(self, transactions: TransactionManager) -> None:
        self._transactions = transactions

    def load_extraction_context(
        self,
        entity_names: list[str],
        task_id: int,
        embed_client,
    ) -> list[EntityContext]:
        with self._transactions.read() as db:
            return _retrieve_entity_context_for_extraction(
                entity_names=entity_names,
                task_id=task_id,
                db=db,
                embed_client=embed_client,
            )

    async def resolve_entity_decisions(
        self,
        decisions: list[dict],
        task_id: int,
        embed_client,
        registry,
        factory,
        profile: str | None = None,
    ) -> ResolvedDecisions:
        with self._transactions.read() as db:
            resolved = await resolve_entities(
                decisions=decisions,
                db=db,
                task_id=task_id,
                embed_client=embed_client,
                registry=registry,
                factory=factory,
                profile=profile,
            )

            canonicalizer_candidates = [
                decision
                for decision in resolved
                if decision["action"] in ("merge", "create")
            ]
            merge_only = [
                decision
                for decision in canonicalizer_candidates
                if decision["action"] == "merge"
            ]
            if settings.CANONICALIZER_ENABLED and merge_only:
                all_target_eids = {
                    decision["target_entity_id"] for decision in merge_only
                }
                all_aliases_map = _fetch_aliases_by_entity(db, all_target_eids)
                for decision in merge_only:
                    decision["all_aliases"] = list(
                        dict.fromkeys(
                            all_aliases_map.get(decision["target_entity_id"], [])
                            + [decision["batch_ref"]]
                        )
                    )

        return ResolvedDecisions(
            decisions=resolved,
            canonicalizer_candidates=canonicalizer_candidates,
        )

    def persist_batch(
        self,
        task_id: int,
        batch_id: str,
        facts: list[dict],
        decisions: list[dict],
        embed_client,
        batch_index: int | None,
        session_number: int | None,
    ) -> None:
        with self._transactions.write() as db:
            ref_to_entity_id = entity_queries.build_ref_map(db, task_id)
            write_batch_results(
                db=db,
                task_id=task_id,
                batch_id=batch_id,
                facts=facts,
                decisions=decisions,
                embed_client=embed_client,
                ref_to_entity_id=ref_to_entity_id,
                batch_index=batch_index,
                session_number=session_number,
            )
