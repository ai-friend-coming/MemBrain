"""Multi-path fact retrieval: BM25 + embedding + entity tree beam search."""

from __future__ import annotations

import logging
import re
from collections import defaultdict

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from membrain.config import settings
from membrain.infra.retrieval.candidate_retrieval import (
    _bm25_search,
    _embedding_search,
    _sanitize_bm25_query,
)
from membrain.retrieval.core.types import (
    RetrievedFact,
    RetrievedMessage,
    RetrievedSession,
)

log = logging.getLogger(__name__)

# Matches [alias] bracket refs; excludes time tokens like [word::DATE] (which contain ':')
_ENTITY_BRACKET_RE = re.compile(r"\[([^\]:#]+)\]")


def _resolve_entity_refs(
    text: str,
    ref_map: dict[str, str],  # alias → canonical
) -> str:
    """Replace [alias] entity bracket refs with canonical names.

    Leaves time tokens ([word::DATE], [2023-05-07]) untouched.
    """

    def _replace(m: re.Match) -> str:
        alias = m.group(1)
        canonical = ref_map.get(alias)
        if canonical is None:
            return m.group(0)
        return canonical

    return _ENTITY_BRACKET_RE.sub(_replace, text)


# ── Path A: BM25 on facts ──


def bm25_search_facts(
    query: str,
    task_id: int,
    db: Session,
    limit: int = settings.QA_BM25_FACT_TOP_N,
) -> list[RetrievedFact]:
    safe_query = _sanitize_bm25_query(query)
    if not safe_query:
        return []
    sql = sa_text("""
        SELECT id, text, pdb.score(id) AS score
        FROM facts
        WHERE search_text ||| :query
          AND task_id = :task_id
          AND status = 'active'
        ORDER BY score DESC
        LIMIT :limit
    """)
    try:
        rows = db.execute(
            sql,
            {"query": safe_query, "task_id": task_id, "limit": limit},
        ).fetchall()
    except Exception:
        log.warning("BM25 fact search failed for query %r", query, exc_info=True)
        return []
    return [RetrievedFact(fact_id=r[0], text=r[1], source="bm25") for r in rows]


def embedding_search_facts(
    query_vec: list[float],
    task_id: int,
    db: Session,
    limit: int = settings.QA_EMBED_FACT_TOP_N,
) -> list[RetrievedFact]:
    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
    sql = sa_text("""
        SELECT id, text,
               -(text_embedding <#> CAST(:vec AS halfvec)) AS score
        FROM facts
        WHERE task_id = :task_id
          AND text_embedding IS NOT NULL
          AND status = 'active'
        ORDER BY text_embedding <#> CAST(:vec AS halfvec)
        LIMIT :limit
    """)
    rows = db.execute(
        sql,
        {"vec": vec_str, "task_id": task_id, "limit": limit},
    ).fetchall()
    return [RetrievedFact(fact_id=r[0], text=r[1], source="embed") for r in rows]


# ── Path C: Entity match → tree beam search ──


def _match_entities(
    query: str,
    query_vec: list[float],
    task_id: int,
    db: Session,
    top_n: int,
) -> list[str]:
    """Find top entities via BM25 on fact_refs + embedding on entity desc."""
    bm25_rows = _bm25_search(query, task_id, db, limit=top_n * 2)
    embed_rows = _embedding_search(query_vec, task_id, db, limit=top_n * 2)
    seen: dict[str, float] = {}
    for row in bm25_rows:
        # _bm25_search returns (alias_text, entity_id) — no score column
        eid = row[1]
        seen[eid] = max(seen.get(eid, 0.0), 1.0)
    for row in embed_rows:
        # _embedding_search returns (entity_id, canonical_ref, desc, score)
        eid = row[0]
        seen[eid] = max(seen.get(eid, 0.0), float(row[3]))
    ranked = sorted(seen.items(), key=lambda x: x[1], reverse=True)
    return [eid for eid, _ in ranked[:top_n]]


def _parse_vec(v) -> list[float]:
    """Parse a pgvector value (string or iterable) to list[float]."""
    if isinstance(v, str):
        return [float(x) for x in v.strip("[]").split(",")]
    return [float(x) for x in v]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _tree_beam_search(
    entity_id: str,
    task_id: int,
    db: Session,
    question_vec: list[float],
    beam_width: int,
) -> list[int]:
    """Walk the entity tree with beam search, return collected fact_ids."""
    sql = sa_text("""
        SELECT id, parent_id, node_type, fact_id, description_embedding
        FROM entity_tree_nodes
        WHERE entity_id = :entity_id AND task_id = :task_id
    """)
    rows = db.execute(
        sql,
        {"entity_id": entity_id, "task_id": task_id},
    ).fetchall()
    if not rows:
        return []

    # Build tree structures
    children_map: dict[int | None, list] = defaultdict(list)
    node_map: dict[int, dict] = {}
    for r in rows:
        node = {
            "id": r[0],
            "parent_id": r[1],
            "node_type": r[2],
            "fact_id": r[3],
            "embedding": r[4],
        }
        node_map[node["id"]] = node
        children_map[r[1]].append(node)

    # Find root(s)
    roots = children_map[None]
    if not roots:
        return []

    collected_fact_ids: list[int] = []
    beam = list(roots)

    while beam:
        next_beam = []
        for node in beam:
            if node["node_type"] == "leaf":
                if node["fact_id"] is not None:
                    collected_fact_ids.append(node["fact_id"])
                continue
            kids = children_map.get(node["id"], [])
            if not kids:
                continue
            all_leaves = all(k["node_type"] == "leaf" for k in kids)
            if all_leaves:
                # Rank leaf children by similarity to question, take top beam_width
                scored = [
                    (
                        _cosine_sim(question_vec, _parse_vec(k["embedding"]))
                        if k["embedding"]
                        else 0.0,
                        k,
                    )
                    for k in kids
                ]
                scored.sort(key=lambda x: x[0], reverse=True)
                for _, k in scored[:beam_width]:
                    if k["fact_id"] is not None:
                        collected_fact_ids.append(k["fact_id"])
            else:
                # Rank non-leaf children by similarity to question
                scored = []
                for k in kids:
                    if k["embedding"] is not None:
                        sim = _cosine_sim(question_vec, _parse_vec(k["embedding"]))
                    else:
                        sim = 0.0
                    scored.append((sim, k))
                scored.sort(key=lambda x: x[0], reverse=True)
                next_beam.extend(k for _, k in scored[:beam_width])
        beam = next_beam

    return collected_fact_ids


def entity_tree_search(
    query: str,
    query_vec: list[float],
    task_id: int,
    db: Session,
    entity_top_n: int = settings.QA_ENTITY_TOP_N,
    beam_width: int = settings.QA_TREE_BEAM_WIDTH,
    limit: int = settings.QA_TREE_FACT_TOP_N,
) -> list[RetrievedFact]:
    """Path C: entity match → tree beam search → collect leaf facts."""
    entity_ids = _match_entities(query, query_vec, task_id, db, entity_top_n)
    if not entity_ids:
        return []

    all_fact_ids: list[int] = []
    for eid in entity_ids:
        fids = _tree_beam_search(eid, task_id, db, query_vec, beam_width)
        all_fact_ids.extend(fids)

    # Dedup and fetch fact texts, rank by embedding similarity
    unique_ids = list(dict.fromkeys(all_fact_ids))
    if not unique_ids:
        return []

    vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"
    sql = sa_text("""
        SELECT id, text,
               -(text_embedding <#> CAST(:vec AS halfvec)) AS score
        FROM facts
        WHERE id = ANY(:ids)
          AND text_embedding IS NOT NULL
          AND status = 'active'
        ORDER BY text_embedding <#> CAST(:vec AS halfvec)
        LIMIT :limit
    """)
    rows = db.execute(
        sql, {"vec": vec_str, "ids": unique_ids, "limit": limit}
    ).fetchall()
    return [RetrievedFact(fact_id=r[0], text=r[1], source="tree") for r in rows]


# ── Path D: BM25 on raw chat_messages ──


def bm25_search_messages(
    query: str,
    task_id: int,
    db: Session,
    limit: int = settings.QA_BM25_MSG_TOP_N,
    speaker: str | None = None,
) -> list[RetrievedMessage]:
    """Search raw chat_messages content using BM25, filtered by task_id.

    Pass ``speaker="assistant"`` to restrict to assistant messages only.
    """
    safe_query = _sanitize_bm25_query(query)
    if not safe_query:
        return []
    speaker_filter = "AND cm.speaker = :speaker" if speaker else ""
    sql = sa_text(f"""
        SELECT cm.id, cm.session_id, cm.speaker, cm.content, cm.message_time,
               paradedb.score(cm.id) AS score
        FROM chat_messages cm
        JOIN chat_sessions cs ON cs.id = cm.session_id
        WHERE cm.id @@@ paradedb.parse(:query)
          AND cs.task_id = :task_id
          {speaker_filter}
        ORDER BY score DESC
        LIMIT :limit
    """)
    try:
        with db.begin_nested():
            rows = db.execute(
                sql,
                {
                    "query": safe_query,
                    "task_id": task_id,
                    "limit": limit,
                    **({"speaker": speaker} if speaker else {}),
                },
            ).fetchall()
    except Exception:
        log.warning("BM25 message search failed for query %r", query, exc_info=True)
        return []
    results = []
    for r in rows:
        msg_time = r[4].isoformat() if r[4] else ""
        results.append(
            RetrievedMessage(
                message_id=r[0],
                session_id=r[1],
                speaker=r[2],
                content=r[3],
                message_time=msg_time,
                bm25_score=float(r[5]),
                query=query,
            )
        )
    return results


# ── Session summary retrieval ──


def bm25_search_sessions(
    query: str,
    task_id: int,
    db: Session,
    limit: int = settings.QA_SESSION_BM25_TOP_N,
) -> list[RetrievedSession]:
    """BM25 search on session_summaries.content."""
    safe_query = _sanitize_bm25_query(query)
    if not safe_query:
        return []
    sql = sa_text("""
        SELECT ss.id, ss.session_id, ss.subject, ss.content, pdb.score(ss.id) AS score,
               cs.session_number
        FROM session_summaries ss
        LEFT JOIN chat_sessions cs ON cs.id = ss.session_id
        WHERE ss.content ||| :query
          AND ss.task_id = :task_id
        ORDER BY score DESC
        LIMIT :limit
    """)
    try:
        with db.begin_nested():
            rows = db.execute(
                sql,
                {"query": safe_query, "task_id": task_id, "limit": limit},
            ).fetchall()
    except Exception:
        return []
    return [
        RetrievedSession(
            session_summary_id=r[0],
            session_id=r[1],
            subject=r[2],
            content=r[3],
            score=r[4],
            source="bm25",
            session_number=r[5],
        )
        for r in rows
    ]


def aggregate_session_scores(
    facts: list[RetrievedFact],
    task_id: int,
    db: Session,
    limit: int = settings.QA_SESSION_FINAL_TOP_N,
) -> list[RetrievedSession]:
    """Score sessions by aggregating rerank scores of their constituent facts.

    Path: facts.session_number → session_summaries.
    """
    if not facts:
        return []

    fact_ids = [f.fact_id for f in facts]
    score_by_fid = {f.fact_id: f.rerank_score for f in facts}

    # Direct column read: session_number is now stored on facts
    mapping_sql = sa_text("""
        SELECT id, session_number
        FROM facts
        WHERE id = ANY(:fact_ids)
          AND session_number IS NOT NULL
          AND status = 'active'
    """)
    mapping_rows = db.execute(
        mapping_sql,
        {"fact_ids": fact_ids},
    ).fetchall()

    if not mapping_rows:
        return []

    # Aggregate scores by session_number
    sess_score: dict[int, float] = defaultdict(float)
    sess_count: dict[int, int] = defaultdict(int)
    for fid, sn in mapping_rows:
        sess_score[sn] += score_by_fid.get(fid, 0.0)
        sess_count[sn] += 1

    # Fetch session_summaries for the top sessions
    from membrain.infra.models.dataset import ChatSessionModel
    from membrain.infra.models.memory import SessionSummaryModel

    ranked_sessions = sorted(sess_score.items(), key=lambda x: x[1], reverse=True)
    top_sess_nums = [sn for sn, _ in ranked_sessions[: limit * 2]]

    rows = (
        db.query(
            SessionSummaryModel.id,
            SessionSummaryModel.session_id,
            SessionSummaryModel.subject,
            SessionSummaryModel.content,
            ChatSessionModel.session_number,
        )
        .join(ChatSessionModel, SessionSummaryModel.session_id == ChatSessionModel.id)
        .filter(
            SessionSummaryModel.task_id == task_id,
            ChatSessionModel.session_number.in_(top_sess_nums),
        )
        .all()
    )

    results = []
    for r in rows:
        sn = r[4]
        results.append(
            RetrievedSession(
                session_summary_id=r[0],
                session_id=r[1],
                subject=r[2],
                content=r[3],
                score=sess_score.get(sn, 0.0),
                source="fact_agg",
                contributing_facts=sess_count.get(sn, 0),
                session_number=sn,
            )
        )

    results.sort(key=lambda s: s.score, reverse=True)
    return results[:limit]


def retrieve_sessions(
    question: str,
    task_id: int,
    db: Session,
    facts: list[RetrievedFact],
    limit: int = settings.QA_SESSION_FINAL_TOP_N,
) -> list[RetrievedSession]:
    """Retrieve session summaries via BM25 + fact-aggregation, deduplicated."""
    path_bm25 = bm25_search_sessions(question, task_id, db)
    path_agg = aggregate_session_scores(facts, task_id, db, limit=limit)

    # Dedup by session_id, first occurrence wins
    seen: dict[int, RetrievedSession] = {}
    for s in path_agg + path_bm25:  # agg first (higher signal)
        if s.session_id not in seen:
            seen[s.session_id] = s

    pool = sorted(seen.values(), key=lambda s: s.score, reverse=True)
    return pool[:limit]
