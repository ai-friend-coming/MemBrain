"""Multi-path retrieval with pluggable fusion strategy.

Six retrieval paths, then fused via either:
  "rrf"    — Reciprocal Rank Fusion (default)
  "rerank" — Cross-encoder reranking

Paths:
  A  — BM25 on facts (keyword-stripped query)
  B  — Embedding on facts (original question)
  B2 — Embedding on facts (HyDE declarative query)
  B3 — Embedding on facts (event-focused query)
  C  — Entity tree beam search
  D  — BM25 parsed query (LLM-generated Tantivy query with AND/OR semantics)

Plus independent session-summary BM25 search and optional agentic round 2.
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from membrain.config import settings
from membrain.infra.clients.bm25_query_gen import generate_bm25_query
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.clients.multi_query import generate_multi_queries
from membrain.infra.clients.query_rewrite import rewrite_query
from membrain.infra.clients.rerank import RerankClient
from membrain.infra.retrieval.aspect_enrichment import (
    aspect_dedup,
    build_aspect_paths,
    enrich_for_rerank,
)
from membrain.infra.retrieval.fact_retrieval import (
    _ENTITY_BRACKET_RE,
    _resolve_entity_refs,
    bm25_search_facts,
    bm25_search_sessions,
    embedding_search_facts,
    entity_tree_search,
    retrieve_sessions,
)
from membrain.retrieval.core.budget_pack import (
    budget_pack,
    estimate_tokens,
    format_session_section,
)
from membrain.retrieval.core.types import AspectInfo, RetrievedFact, RetrievedSession

log = logging.getLogger(__name__)

_RRF_K = 60

# Default budgets
_FACT_BUDGET_TOKENS = 4500
_SESSION_BUDGET_TOKENS = 1500
_ROUND2_EXTRA_TOKENS = 1000


# ── Fusion sub-functions ─────────────────────────────────────────────────────


def _fuse_rrf(
    pool: list[RetrievedFact],
    ranked_lists: list[list[int]],
) -> None:
    """Reciprocal Rank Fusion — assigns rerank_score to each fact in pool."""
    rank_maps: list[dict[int, int]] = []
    for lst in ranked_lists:
        rank_maps.append({fid: i for i, fid in enumerate(lst)})
    for fact in pool:
        score = 0.0
        for rm in rank_maps:
            rank = rm.get(fact.fact_id)
            if rank is not None:
                score += 1.0 / (_RRF_K + rank + 1)
        fact.rerank_score = score


def _fuse_rerank(
    question: str,
    pool: list[RetrievedFact],
    aspect_infos: dict[int, AspectInfo],
    top_k: int,
) -> list[RetrievedFact]:
    """Cross-encoder reranking — returns top_k facts sorted by relevance score."""
    rerank_client = RerankClient()
    try:
        enriched = [
            enrich_for_rerank(f.text, aspect_infos.get(f.fact_id), f.time_info)
            for f in pool
        ]
        ranked = rerank_client.rerank(question, enriched, top_n=top_k)
        results = []
        for item in ranked:
            f = pool[item["index"]]
            f.rerank_score = item["relevance_score"]
            results.append(f)
        return results
    except Exception:
        log.warning("Rerank failed, falling back to pool order")
        return pool[:top_k]
    finally:
        rerank_client.close()


# ── Path D: Conjunctive ILIKE fact search ────────────────────────────────────


def _bm25_parsed_search_facts(
    query: str,
    task_id: int,
    db: Session,
    limit: int = 20,
) -> list[RetrievedFact]:
    """Search facts using a Tantivy query string via pdb.parse()."""
    if not query:
        return []
    sql = sa_text("""
        SELECT id, text, pdb.score(id) AS score
        FROM facts
        WHERE id @@@ pdb.parse(:query, lenient => true)
          AND task_id = :task_id
          AND status = 'active'
        ORDER BY score DESC
        LIMIT :limit
    """)
    try:
        rows = db.execute(
            sql, {"query": query, "task_id": task_id, "limit": limit}
        ).fetchall()
    except Exception:
        log.debug("BM25 parsed fact search failed for query %r", query, exc_info=True)
        return []
    return [RetrievedFact(fact_id=r[0], text=r[1], source="bm25_parsed") for r in rows]


def _bm25_parsed_search_sessions(
    query: str,
    task_id: int,
    db: Session,
    limit: int = 5,
) -> list[RetrievedSession]:
    """Search session summaries using a Tantivy query string via pdb.parse()."""
    if not query:
        return []
    sql = sa_text("""
        SELECT ss.id, ss.session_id, ss.subject, ss.content,
               pdb.score(ss.id) AS score, cs.session_number
        FROM session_summaries ss
        LEFT JOIN chat_sessions cs ON cs.id = ss.session_id
        WHERE ss.id @@@ pdb.parse(:query, lenient => true)
          AND ss.task_id = :task_id
        ORDER BY score DESC
        LIMIT :limit
    """)
    try:
        rows = db.execute(
            sql, {"query": query, "task_id": task_id, "limit": limit}
        ).fetchall()
    except Exception:
        log.debug(
            "BM25 parsed session search failed for query %r", query, exc_info=True
        )
        return []
    return [
        RetrievedSession(
            session_summary_id=r[0],
            session_id=r[1],
            subject=r[2] or "",
            content=r[3],
            score=r[4],
            source="bm25_parsed",
            session_number=r[5],
        )
        for r in rows
    ]


# ── Post-processing helpers ──────────────────────────────────────────────────


def _inject_time_annotations(pool: list[RetrievedFact], db: Session) -> None:
    if not pool:
        return
    fact_ids = [f.fact_id for f in pool]
    rows = db.execute(
        sa_text(
            "SELECT fact_id, time_raw, time_resolved FROM time_annotations "
            "WHERE fact_id = ANY(:ids)"
        ),
        {"ids": fact_ids},
    ).fetchall()
    time_map: dict[int, str] = {}
    for fid, raw, resolved in rows:
        if not raw and not resolved:
            continue
        parts: list[str] = []
        if raw:
            parts.append(str(raw))
        if resolved:
            start, *rest = resolved.split("/", 1)
            end = rest[0] if rest else None
            parts.append(f"[{start}, {end}]" if end is not None else f"[{start}]")
        time_map[fid] = " ".join(parts)

    no_time_ids = [f.fact_id for f in pool if f.fact_id not in time_map]
    if no_time_ids:
        for r in db.execute(
            sa_text(
                "SELECT id, fact_ts FROM facts WHERE id = ANY(:ids) "
                "AND fact_ts IS NOT NULL"
            ),
            {"ids": no_time_ids},
        ).fetchall():
            time_map[r[0]] = f"[{r[1]}]"

    for fact in pool:
        fact.time_info = time_map.get(fact.fact_id, "")


def _inject_session_numbers(pool: list[RetrievedFact], db: Session) -> None:
    if not pool:
        return
    fact_ids = [f.fact_id for f in pool]
    rows = db.execute(
        sa_text("SELECT id, session_number FROM facts WHERE id = ANY(:ids)"),
        {"ids": fact_ids},
    ).fetchall()
    sn_map = {r[0]: r[1] for r in rows if r[1] is not None}
    for fact in pool:
        fact.session_number = sn_map.get(fact.fact_id)


def _resolve_pool_entity_refs(
    pool: list[RetrievedFact],
    db: Session,
) -> None:
    all_aliases: set[str] = set()
    for fact in pool:
        for alias in _ENTITY_BRACKET_RE.findall(fact.text):
            all_aliases.add(alias)
    if not all_aliases:
        return
    rows = db.execute(
        sa_text("""
            SELECT DISTINCT ON (fr.alias_text)
                   fr.alias_text, e.canonical_ref
            FROM fact_refs fr
            JOIN entities e ON e.entity_id = fr.entity_id
            WHERE fr.alias_text = ANY(:texts)
            ORDER BY fr.alias_text
        """),
        {"texts": list(all_aliases)},
    ).fetchall()
    alias_canonical = {r[0]: r[1] for r in rows}
    if not alias_canonical:
        return
    for fact in pool:
        fact.text = _resolve_entity_refs(fact.text, alias_canonical)


# ── Agentic round 2 ─────────────────────────────────────────────────────────


_REFLECT_SYSTEM = """\
You are analysing retrieved memory facts to identify what is still missing.
Given a question and the top retrieved facts, output ONLY valid JSON:
{
  "sufficient": true/false,
  "refined_queries": ["query1", "query2"]
}
- Set sufficient=true if the facts clearly contain enough to answer the question.
- Otherwise provide 1-2 targeted queries that search for the missing information.
  Focus on specific entity names or events not yet found. Max 20 words each.
No explanation outside the JSON.\
"""


def _reflect_and_refine(
    question: str,
    facts: list[RetrievedFact],
    http_client: httpx.Client,
) -> list[str]:
    """Return 0-2 refined queries if round-1 facts are insufficient."""
    facts_text = "\n".join(
        f"- {f.text}" + (f" ({f.time_info})" if f.time_info else "") for f in facts[:20]
    )
    try:
        resp = http_client.post(
            f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
            json={
                "model": settings.QA_LLM_MODEL,
                "messages": [
                    {"role": "system", "content": _REFLECT_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\n"
                            f"Retrieved facts ({len(facts)}):\n{facts_text}"
                        ),
                    },
                ],
                "max_tokens": 150,
                "temperature": 0.0,
            },
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        import json

        data = json.loads(raw)
        if data.get("sufficient"):
            return []
        queries = data.get("refined_queries", [])
        if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
            return queries[:2]
    except Exception:
        log.debug("reflection failed", exc_info=True)
    return []


# ── Main entry point ─────────────────────────────────────────────────────────


def search(
    question: str,
    task_id: int,
    db: Session,
    embed_client: EmbeddingClient,
    http_client: httpx.Client,
    top_k: int = settings.QA_RERANK_TOP_K,
    strategy: Literal["rrf", "rerank"] = "rrf",
    mode: Literal["direct", "expand", "reflect"] = "expand",
) -> dict:
    """Multi-path retrieval with pluggable fusion strategy.

    mode="direct"  — 3-path (A+B+C), no LLM query rewriting
    mode="expand"  — 6-path (A+B+B2+B3+C+D) with LLM query expansion (default)
    mode="reflect" — 6-path + agentic round-2 reflect-and-refine

    strategy="rrf"    — Reciprocal Rank Fusion (default)
    strategy="rerank" — Cross-encoder reranking

    Returns dict with keys: packed_context, packed_token_count,
    fact_ids, facts, sessions, raw_messages.
    """
    # ── 1. Query rewrite + multi-query expansion ─────────────────────────
    if mode == "direct":
        rewritten = question
        q_event = q_hyde = q_bm25_kw = q_bm25_parsed = ""
    else:
        rewritten = rewrite_query(question, http_client)
        extra_queries = generate_multi_queries(question, http_client)
        q_event = extra_queries[0] if len(extra_queries) > 0 else ""
        q_hyde = extra_queries[1] if len(extra_queries) > 1 else ""
        q_bm25_kw = extra_queries[2] if len(extra_queries) > 2 else rewritten
        q_bm25_parsed = generate_bm25_query(question, http_client)

    try:
        orig_vec = embed_client.embed_single(question)
    except Exception:
        log.warning("Embedding failed for original question")
        orig_vec = None

    try:
        hyde_vec = embed_client.embed_single(q_hyde) if q_hyde else orig_vec
    except Exception:
        hyde_vec = orig_vec

    try:
        rewrite_vec = (
            embed_client.embed_single(rewritten) if rewritten != question else orig_vec
        )
    except Exception:
        rewrite_vec = orig_vec

    # ── 2. Six retrieval paths ────────────────────────────────────────────
    bm25_query = q_bm25_kw or rewritten
    path_a = bm25_search_facts(
        bm25_query, task_id, db, limit=settings.QA_BM25_FACT_TOP_N
    )

    if rewritten != bm25_query:
        extra_bm25 = bm25_search_facts(rewritten, task_id, db, limit=10)
        a_ids = {f.fact_id for f in path_a}
        path_a = path_a + [f for f in extra_bm25 if f.fact_id not in a_ids]

    path_b = embedding_search_facts(orig_vec, task_id, db) if orig_vec else []

    path_b2: list[RetrievedFact] = []
    if hyde_vec and hyde_vec is not orig_vec:
        path_b2 = embedding_search_facts(
            hyde_vec, task_id, db, limit=settings.QA_EMBED_FACT_TOP_N
        )

    path_b3: list[RetrievedFact] = []
    if q_event:
        try:
            event_vec = embed_client.embed_single(q_event)
            if event_vec:
                path_b3 = embedding_search_facts(event_vec, task_id, db, limit=15)
        except Exception:
            pass

    path_c: list[RetrievedFact] = []
    if rewrite_vec:
        path_c = entity_tree_search(question, rewrite_vec, task_id, db)
        if path_c:
            c_ids = [f.fact_id for f in path_c]
            c_aspects = build_aspect_paths(c_ids, task_id, db)
            kept = set(aspect_dedup(c_ids, c_aspects))
            path_c = [f for f in path_c if f.fact_id in kept]

    path_d: list[RetrievedFact] = []
    if q_bm25_parsed:
        path_d = _bm25_parsed_search_facts(q_bm25_parsed, task_id, db, limit=20)

    # ── 3. Dedup into pool ────────────────────────────────────────────────
    ranked_lists = [
        [f.fact_id for f in path_a],
        [f.fact_id for f in path_b],
        [f.fact_id for f in path_b2],
        [f.fact_id for f in path_b3],
        [f.fact_id for f in path_c],
        [f.fact_id for f in path_d],
    ]
    seen: dict[int, RetrievedFact] = {}
    for lst in (path_a, path_b, path_b2, path_b3, path_c, path_d):
        for f in lst:
            if f.fact_id not in seen:
                seen[f.fact_id] = f
    pool = list(seen.values())

    if not pool:
        return _empty_result()

    # ── 4. Post-processing ────────────────────────────────────────────────
    _inject_time_annotations(pool, db)
    _inject_session_numbers(pool, db)

    all_fact_ids = [f.fact_id for f in pool]
    aspect_infos = build_aspect_paths(all_fact_ids, task_id, db)
    for fact in pool:
        info = aspect_infos.get(fact.fact_id)
        if info:
            fact.entity_ref = info.entity_ref
            fact.aspect_path = info.path
            fact.aspect_summary = info.leaf_desc

    # ── 5. Fusion ─────────────────────────────────────────────────────────
    if strategy == "rerank":
        round1_facts = _fuse_rerank(question, pool, aspect_infos, top_k)
    else:
        _fuse_rrf(pool, ranked_lists)
        pool.sort(key=lambda f: f.rerank_score, reverse=True)
        round1_facts = pool[:top_k]

    # ── 6. Agentic round 2 (reflect mode only) ────────────────────────────
    if mode == "reflect" and http_client:
        refined_queries = _reflect_and_refine(question, round1_facts, http_client)
        if refined_queries:
            extra_pool: list[RetrievedFact] = []
            seen_ids = {f.fact_id for f in pool}
            for rq in refined_queries:
                for f in bm25_search_facts(rq, task_id, db, limit=15):
                    if f.fact_id not in seen_ids:
                        extra_pool.append(f)
                        seen_ids.add(f.fact_id)
                try:
                    rq_vec = embed_client.embed_single(rq)
                    if rq_vec:
                        for f in embedding_search_facts(rq_vec, task_id, db, limit=15):
                            if f.fact_id not in seen_ids:
                                extra_pool.append(f)
                                seen_ids.add(f.fact_id)
                except Exception:
                    pass

            if extra_pool:
                _inject_time_annotations(extra_pool, db)
                _inject_session_numbers(extra_pool, db)
                extra_ids = [f.fact_id for f in extra_pool]
                extra_aspects = build_aspect_paths(extra_ids, task_id, db)
                aspect_infos.update(extra_aspects)
                for fact in extra_pool:
                    info = extra_aspects.get(fact.fact_id)
                    if info:
                        fact.entity_ref = info.entity_ref
                        fact.aspect_path = info.path
                        fact.aspect_summary = info.leaf_desc
                round1_facts = round1_facts + extra_pool
                log.debug("agentic round 2: +%d facts", len(extra_pool))

    # Resolve entity bracket refs on the final selected facts so that the
    # first occurrence in the output gets the description appended.
    _resolve_pool_entity_refs(round1_facts, db)

    # ── 7. Session retrieval ──────────────────────────────────────────────
    seen_sess: dict[int, RetrievedSession] = {}
    for sq in [bm25_query, q_hyde, question]:
        if not sq:
            continue
        for s in bm25_search_sessions(sq, task_id, db, limit=8):
            if s.session_id not in seen_sess:
                seen_sess[s.session_id] = s

    if q_bm25_parsed:
        for s in _bm25_parsed_search_sessions(q_bm25_parsed, task_id, db, limit=5):
            if s.session_id not in seen_sess:
                seen_sess[s.session_id] = s

    for s in retrieve_sessions(question, task_id, db, round1_facts, limit=6):
        if s.session_id not in seen_sess:
            seen_sess[s.session_id] = s

    sessions = sorted(seen_sess.values(), key=lambda s: s.score, reverse=True)[:10]

    # ── 8. Pack context ───────────────────────────────────────────────────
    packed = budget_pack(round1_facts, max_tokens=_FACT_BUDGET_TOKENS)

    session_section = format_session_section(sessions, _SESSION_BUDGET_TOKENS)
    if session_section:
        packed.text = session_section + "\n\n" + packed.text
        packed.token_count += estimate_tokens(session_section)

    return {
        "packed_context": packed.text,
        "packed_token_count": packed.token_count,
        "fact_ids": packed.fact_ids,
        "facts": [
            {
                "fact_id": f.fact_id,
                "text": f.text,
                "source": f.source,
                "rerank_score": f.rerank_score,
                "time_info": f.time_info,
                "entity_ref": f.entity_ref,
                "aspect_path": f.aspect_path,
            }
            for f in round1_facts
        ],
        "sessions": [
            {
                "session_summary_id": s.session_summary_id,
                "session_id": s.session_id,
                "subject": s.subject,
                "content": s.content,
                "score": s.score,
                "source": s.source,
                "contributing_facts": s.contributing_facts,
            }
            for s in sessions
        ],
        "raw_messages": [],
    }


def _empty_result() -> dict:
    return {
        "packed_context": "",
        "packed_token_count": 0,
        "fact_ids": [],
        "facts": [],
        "sessions": [],
        "raw_messages": [],
    }
