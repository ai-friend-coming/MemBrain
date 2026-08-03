"""Database-backed adapters for the memory ingestion pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text as sa_text

from membrain.config import settings
from membrain.infra.models.memory import FactModel, FactRefModel, FactStatus
from membrain.infra.persistence.batch_writer import (
    _extract_bracket_refs,
    write_batch_results,
)
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

_FACT_EQUIVALENCE_CANDIDATE_TOP_K = 5
_FACT_EQUIVALENCE_MIN_SIMILARITY = 0.6


def _match_exact_fact_ids(
    facts: list[dict],
    decisions: list[dict],
    exact_rows: list[tuple[int, str]],
    fact_ref_rows: list[tuple[int, str, str]],
) -> list[int | None]:
    """按文本和稳定实体身份选择可直接复用的事实。

    Args:
        facts: 当前批次抽取的新事实。
        decisions: 当前批次已完成的实体解析决策。
        exact_rows: 文本相同的已存事实 ID 和文本。
        fact_ref_rows: 已存事实的实体 ID 和原始引用。

    Returns:
        list[int | None]: 与新事实顺序对齐的精确命中 ID。
    """
    entity_id_by_ref = {
        decision["batch_ref"]: decision.get("target_entity_id")
        or f"new:{decision['batch_ref']}"
        for decision in decisions
    }
    fact_ids_by_text: dict[str, list[int]] = {}
    for fact_id, fact_text in exact_rows:
        fact_ids_by_text.setdefault(fact_text, []).append(fact_id)

    stored_ids_by_ref: dict[tuple[int, str], set[str]] = {}
    for fact_id, entity_id, alias_text in fact_ref_rows:
        stored_ids_by_ref.setdefault((fact_id, alias_text), set()).add(entity_id)

    exact_ids: list[int | None] = []
    for fact in facts:
        refs = _extract_bracket_refs(fact["text"])
        matched_id = None
        for candidate_id in fact_ids_by_text.get(fact["text"], []):
            if not refs or all(
                entity_id_by_ref.get(ref) is not None
                and stored_ids_by_ref.get((candidate_id, ref))
                == {entity_id_by_ref[ref]}
                for ref in refs
            ):
                matched_id = candidate_id
                break
        exact_ids.append(matched_id)
    return exact_ids


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

    def load_fact_equivalence_candidates(
        self,
        facts: list[dict],
        decisions: list[dict],
        task_id: int,
        embed_client,
    ) -> tuple[list[int | None], list[list[dict]]]:
        """优先精确匹配事实，再为其余事实召回语义候选。

        Args:
            facts: 当前批次抽取的新事实。
            decisions: 当前批次已完成的实体解析决策。
            task_id: 事实所属任务主键。
            embed_client: 事实向量客户端。

        Returns:
            tuple[list[int | None], list[list[dict]]]: 与新事实顺序对齐的精确
            命中 ID 和语义候选列表。
        """
        if not facts:
            return [], []

        fact_texts = list(dict.fromkeys(fact["text"] for fact in facts))
        with self._transactions.read() as db:
            exact_rows = (
                db.query(FactModel.id, FactModel.text)
                .filter(
                    FactModel.task_id == task_id,
                    FactModel.status == FactStatus.ACTIVE,
                    FactModel.text.in_(fact_texts),
                )
                .order_by(FactModel.id)
                .all()
            )
            exact_fact_ids = [fact_id for fact_id, _ in exact_rows]
            exact_ref_rows = (
                db.query(
                    FactRefModel.fact_id,
                    FactRefModel.entity_id,
                    FactRefModel.alias_text,
                )
                .filter(FactRefModel.fact_id.in_(exact_fact_ids))
                .all()
                if exact_fact_ids
                else []
            )

        exact_ids = _match_exact_fact_ids(
            facts,
            decisions,
            exact_rows,
            exact_ref_rows,
        )
        unresolved = [
            (index, fact)
            for index, fact in enumerate(facts)
            if exact_ids[index] is None
        ]
        candidates: list[list[dict]] = [[] for _ in facts]
        if not unresolved:
            return exact_ids, candidates

        try:
            vectors = embed_client.embed([fact["text"] for _, fact in unresolved])
        except Exception:
            log.warning("事实等价候选向量生成失败", exc_info=True)
            return exact_ids, candidates

        sql = sa_text("""
            SELECT id, text,
                   -(text_embedding <#> CAST(:vec AS halfvec)) AS similarity
            FROM facts
            WHERE task_id = :task_id
              AND text_embedding IS NOT NULL
              AND status = 'active'
              AND -(text_embedding <#> CAST(:vec AS halfvec)) >= :min_similarity
            ORDER BY text_embedding <#> CAST(:vec AS halfvec)
            LIMIT :limit
        """)
        candidate_rows: dict[int, list[tuple[int, str]]] = {}
        with self._transactions.read() as db:
            for (index, _), vector in zip(unresolved, vectors):
                vector_literal = "[" + ",".join(str(value) for value in vector) + "]"
                rows = db.execute(
                    sql,
                    {
                        "vec": vector_literal,
                        "task_id": task_id,
                        "min_similarity": _FACT_EQUIVALENCE_MIN_SIMILARITY,
                        "limit": _FACT_EQUIVALENCE_CANDIDATE_TOP_K,
                    },
                ).fetchall()
                candidate_rows[index] = [(row[0], row[1]) for row in rows]

            candidate_ids = {
                fact_id for rows in candidate_rows.values() for fact_id, _ in rows
            }
            refs_by_fact: dict[int, list[dict]] = {}
            if candidate_ids:
                for fact_id, entity_id, alias_text in (
                    db.query(
                        FactRefModel.fact_id,
                        FactRefModel.entity_id,
                        FactRefModel.alias_text,
                    )
                    .filter(FactRefModel.fact_id.in_(candidate_ids))
                    .all()
                ):
                    refs_by_fact.setdefault(fact_id, []).append(
                        {"ref": alias_text, "entity_id": entity_id}
                    )

        for index, rows in candidate_rows.items():
            candidates[index] = [
                {
                    "fact_id": fact_id,
                    "text": fact_text,
                    "entities": refs_by_fact.get(fact_id, []),
                }
                for fact_id, fact_text in rows
            ]
        return exact_ids, candidates

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
