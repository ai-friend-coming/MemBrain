"""PersonaMem V2 dataset evaluation profile — MCQ-aware retrieval."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text as sa_text

from evaluation.answering.profiles.base import BaseEvalProfile
from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.agents.retry import run_agent_with_retry
from membrain.config import settings
from membrain.infra.clients.rerank import RerankClient
from membrain.infra.retrieval.fact_retrieval import (
    bm25_search_facts,
    embedding_search_facts,
    entity_tree_search,
)
from membrain.retrieval.core.types import RetrievedFact

if TYPE_CHECKING:
    from evaluation.models.qa import QAPairModel
    from evaluation.runtime.local_search import LocalSearchRunner

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MANIFESTS_DIR = str(_PROJECT_ROOT / "manifests")


class PersonaMemV2Profile(BaseEvalProfile):
    # Per-query rerank top-N for *positive* facts per section.
    # Increased from 5→7 now that DNU facts no longer occupy section slots.
    _RERANK_TOP_N: int = 7
    use_exact_match: bool = True  # MCQ dataset — judge by exact letter match

    def __init__(self, ranker: str = "rrf") -> None:
        super().__init__(ranker)
        self._registry = TaskRegistry(_MANIFESTS_DIR)
        self._factory = AgentFactory(
            self._registry, settings.LLM_API_URL, settings.LLM_API_KEY
        )
        self._rerank_client = RerankClient()

    # ── Internal helpers ──────────────────────────────────────────────────────

    _RRF_K: int = 60  # standard RRF constant

    def _rrf_merge(
        self,
        path_a: list[RetrievedFact],
        path_b: list[RetrievedFact],
        path_c: list[RetrievedFact],
        pos_pool: list[RetrievedFact],
    ) -> list[RetrievedFact]:
        """Reciprocal Rank Fusion over three ranked lists, return top-N positive facts.

        Each path contributes 1/(k + rank) to the RRF score of a fact.
        Only facts already in pos_pool are considered (DNU already removed).
        """
        pos_ids = {f.fact_id for f in pos_pool}
        id_to_fact = {f.fact_id: f for f in pos_pool}
        scores: dict[int, float] = {}

        for path in (path_a, path_b, path_c):
            for rank, f in enumerate(path, start=1):
                if f.fact_id not in pos_ids:
                    continue
                scores[f.fact_id] = scores.get(f.fact_id, 0.0) + 1.0 / (
                    self._RRF_K + rank
                )

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        results = []
        for fid, score in ranked[: self._RERANK_TOP_N]:
            f = id_to_fact[fid]
            f.rerank_score = score
            results.append(f)
        return results

    async def _async_rewrite_choices(self, question: str, options: dict) -> object:
        """Call choice-query-rewriter agent, returns ReturnType(.A .B .C .D)."""
        agent, agent_settings = self._factory.get_agent(
            "choice-query-rewriter", profile="personamemv2"
        )
        prompts = self._registry.render_prompts(
            "choice-query-rewriter",
            profile="personamemv2",
            question=question,
            choice_a=options.get("A", ""),
            choice_b=options.get("B", ""),
            choice_c=options.get("C", ""),
            choice_d=options.get("D", ""),
        )
        result = await run_agent_with_retry(
            agent, instructions=prompts, model_settings=agent_settings
        )
        return result.output

    def _run_per_query(
        self,
        bm25_query: str,
        task_pk: int,
        db,
        embed_client,
        embed_text: str | None = None,
        rerank_query: str | None = None,
    ) -> tuple[list[RetrievedFact], list[RetrievedFact]]:
        """Three-path retrieval + rerank for a single query.

        Returns (pos_facts, dnu_pool) where:
          pos_facts  — top-N reranked positive facts for this section
          dnu_pool   — all DNU facts found (caller accumulates globally)

        Args:
            bm25_query:   Compact keyword query for BM25 and entity-tree search.
            embed_text:   Text to embed for vector search. Falls back to bm25_query.
            rerank_query: Query passed to the reranker. Falls back to bm25_query.
                          For choice sections, pass the full option text so the
                          cross-encoder can score facts against the actual option.
        """
        text_for_embed = embed_text if embed_text else bm25_query
        rq = rerank_query if rerank_query else bm25_query
        try:
            query_vec = embed_client.embed_single(text_for_embed)
        except Exception:
            log.warning("Embedding failed for query %r", text_for_embed)
            query_vec = None

        path_a = bm25_search_facts(bm25_query, task_pk, db)
        path_b = embedding_search_facts(query_vec, task_pk, db) if query_vec else []
        path_c = (
            entity_tree_search(bm25_query, query_vec, task_pk, db) if query_vec else []
        )

        # Dedup by fact_id (first occurrence wins for source tag)
        seen: dict[int, RetrievedFact] = {}
        for f in path_a + path_b + path_c:
            if f.fact_id not in seen:
                seen[f.fact_id] = f
        pool = list(seen.values())
        if not pool:
            return [], []

        _DNU_MARKER = "Do Not Use:"
        dnu_pool = [f for f in pool if f.text.lstrip("- ").startswith(_DNU_MARKER)]
        pos_pool = [f for f in pool if not f.text.lstrip("- ").startswith(_DNU_MARKER)]

        if self.ranker == "rrf":
            results = self._rrf_merge(path_a, path_b, path_c, pos_pool)
        else:
            # Rerank positive facts using rerank_query (option text for choice sections)
            try:
                ranked = self._rerank_client.rerank(
                    rq, [f.text for f in pos_pool], top_n=self._RERANK_TOP_N
                )
            except Exception:
                log.warning("Rerank failed for query %r", rq)
                return pos_pool[: self._RERANK_TOP_N], dnu_pool

            results = []
            for item in ranked:
                f = pos_pool[item["index"]]
                f.rerank_score = item["relevance_score"]
                results.append(f)
        return results, dnu_pool

    def _run_per_query_debug(
        self,
        bm25_query: str,
        task_pk: int,
        db,
        embed_client,
        embed_text: str | None = None,
        rerank_query: str | None = None,
    ) -> tuple[
        list[RetrievedFact],
        list[RetrievedFact],
        list[RetrievedFact],
        list[RetrievedFact],
        list[RetrievedFact],
    ]:
        """Three-path retrieval + rerank. Returns (path_a, path_b, path_c, reranked, dnu_pool)."""
        text_for_embed = embed_text if embed_text else bm25_query
        rq = rerank_query if rerank_query else bm25_query
        try:
            query_vec = embed_client.embed_single(text_for_embed)
        except Exception:
            log.warning("Embedding failed for query %r", text_for_embed)
            query_vec = None

        path_a = bm25_search_facts(bm25_query, task_pk, db)
        path_b = embedding_search_facts(query_vec, task_pk, db) if query_vec else []
        path_c = (
            entity_tree_search(bm25_query, query_vec, task_pk, db) if query_vec else []
        )

        seen: dict[int, RetrievedFact] = {}
        for f in path_a + path_b + path_c:
            if f.fact_id not in seen:
                seen[f.fact_id] = f
        pool = list(seen.values())
        if not pool:
            return path_a, path_b, path_c, [], []

        _DNU_MARKER = "Do Not Use:"
        dnu_pool = [f for f in pool if f.text.lstrip("- ").startswith(_DNU_MARKER)]
        pos_pool = [f for f in pool if not f.text.lstrip("- ").startswith(_DNU_MARKER)]

        if self.ranker == "rrf":
            results = self._rrf_merge(path_a, path_b, path_c, pos_pool)
        else:
            try:
                ranked = self._rerank_client.rerank(
                    rq, [f.text for f in pos_pool], top_n=self._RERANK_TOP_N
                )
            except Exception:
                log.warning("Rerank failed for query %r", rq)
                return path_a, path_b, path_c, pos_pool[: self._RERANK_TOP_N], dnu_pool

            results = []
            for item in ranked:
                f = pos_pool[item["index"]]
                f.rerank_score = item["relevance_score"]
                results.append(f)
        return path_a, path_b, path_c, results, dnu_pool

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def _render_context(
        per_query_facts: list[tuple[str, list[RetrievedFact]]],
        global_dnu: list[RetrievedFact] | None = None,
        shared_fact_ids: set[int] | None = None,
    ) -> str:
        """Render per-query results directly — no merging, no token budgeting.

        Output format:
            [Question]
            - fact text
            - ...

            [Choice A]
            - [shared] fact text   ← appears in 2+ choice sections
            - fact text
            - ...

            [Constraints]
            - Do Not Use: ...
        """
        _CHOICE_KEYS = {"CHOICE A", "CHOICE B", "CHOICE C", "CHOICE D"}
        sections: list[str] = []
        label_map = {
            "QUESTION": "Question",
            "CHOICE A": "Choice A",
            "CHOICE B": "Choice B",
            "CHOICE C": "Choice C",
            "CHOICE D": "Choice D",
        }
        for label, facts in per_query_facts:
            key = label.split(":")[0].strip().upper()
            is_choice = key in _CHOICE_KEYS
            heading = label_map.get(key, label)
            lines = [f"[{heading}]"]
            for f in facts:
                if is_choice and shared_fact_ids and f.fact_id in shared_fact_ids:
                    lines.append(f"- [shared] {f.text}")
                else:
                    lines.append(f"- {f.text}")
            sections.append("\n".join(lines))

        if global_dnu:
            seen_ids: set[int] = set()
            lines = ["[Constraints]"]
            for f in global_dnu:
                if f.fact_id not in seen_ids:
                    seen_ids.add(f.fact_id)
                    lines.append(f"- {f.text}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def retrieve(
        self,
        client: "LocalSearchRunner",
        task_pk: int,
        qa: "QAPairModel",
        top_k: int,
        run_tag: str,
    ) -> str:
        """MCQ-aware retrieval: 5 queries × 3-path → per-query rerank → render."""
        per_query_facts, global_dnu = self._retrieve_per_query(
            client, task_pk, qa, run_tag
        )

        # Identify fact_ids that appear in 2+ choice sections — they carry no
        # discriminating signal and should be marked [shared] for the answerer.
        _CHOICE_KEYS = {"CHOICE A", "CHOICE B", "CHOICE C", "CHOICE D"}
        fact_id_count: dict[int, int] = {}
        for label, facts in per_query_facts:
            if label.split(":")[0].strip().upper() in _CHOICE_KEYS:
                for f in facts:
                    fact_id_count[f.fact_id] = fact_id_count.get(f.fact_id, 0) + 1
        shared_fact_ids = {fid for fid, cnt in fact_id_count.items() if cnt >= 2}

        return self._render_context(
            per_query_facts, global_dnu, shared_fact_ids or None
        )

    @staticmethod
    def _parse_context_sections(context_text: str) -> dict[str, str]:
        """Parse rendered context into per-section strings.

        Input format (from _render_context):
            [Question]
            - fact...

            [Choice A]
            - fact...

            [Constraints]
            - Do Not Use: ...

        Returns dict with keys: QUESTION, A, B, C, D, CONSTRAINTS (empty if missing).
        """
        key_map = {
            "question": "QUESTION",
            "choice a": "A",
            "choice b": "B",
            "choice c": "C",
            "choice d": "D",
            "constraints": "CONSTRAINTS",
        }
        result: dict[str, str] = {v: "" for v in key_map.values()}
        current_key: str | None = None
        buffer: list[str] = []

        for block in context_text.split("\n\n"):
            stripped = block.strip()
            if not stripped:
                continue
            header_line = stripped.splitlines()[0]
            if header_line.startswith("[") and header_line.endswith("]"):
                if current_key is not None:
                    result[current_key] = "\n".join(buffer).strip()
                raw = header_line[1:-1].lower().split(":")[0].strip()
                current_key = key_map.get(raw)
                buffer = ["\n".join(stripped.splitlines()[1:])]
            elif current_key is not None:
                buffer.append(block)

        if current_key is not None:
            result[current_key] = "\n".join(buffer).strip()
        return result

    def generate_answer(
        self,
        qa: "QAPairModel",
        context_text: str,
        model: str,
        http_client,
    ) -> str:
        """Override: use mcq-answerer agent instead of direct HTTP call."""
        options: dict = json.loads(qa.options) if qa.options else {}
        sections = self._parse_context_sections(context_text)
        constraints = sections.get("CONSTRAINTS", "")

        agent, agent_settings = self._factory.get_agent(
            "mcq-answerer", profile="personamemv2"
        )
        prompts = self._registry.render_prompts(
            "mcq-answerer",
            profile="personamemv2",
            question=qa.question,
            choice_a=options.get("A", ""),
            choice_b=options.get("B", ""),
            choice_c=options.get("C", ""),
            choice_d=options.get("D", ""),
            facts_question=sections["QUESTION"],
            facts_a=sections["A"],
            facts_b=sections["B"],
            facts_c=sections["C"],
            facts_d=sections["D"],
            constraints=constraints,
        )
        result = asyncio.run(
            run_agent_with_retry(
                agent, instructions=prompts, model_settings=agent_settings
            )
        )
        return result.output.answer

    def retrieve_debug(
        self,
        client: "LocalSearchRunner",
        task_pk: int,
        qa: "QAPairModel",
        run_tag: str = "default",
    ) -> list[
        tuple[
            str,
            str,
            list[RetrievedFact],
            list[RetrievedFact],
            list[RetrievedFact],
            list[RetrievedFact],
        ]
    ]:
        """Return per-query retrieval debug info.

        Returns list of (label, query_text, path_a, path_b, path_c, reranked).
        """
        return self._retrieve_per_query_debug(client, task_pk, qa, run_tag)

    def _retrieve_per_query_debug(
        self,
        client: "LocalSearchRunner",
        task_pk: int,
        qa: "QAPairModel",
        run_tag: str,
    ) -> list[
        tuple[
            str,
            str,
            list[RetrievedFact],
            list[RetrievedFact],
            list[RetrievedFact],
            list[RetrievedFact],
        ]
    ]:
        """Debug retrieval: returns (label, query_text, path_a, path_b, path_c, reranked)."""
        options: dict = json.loads(qa.options) if qa.options else {}

        try:
            rewritten = asyncio.run(self._async_rewrite_choices(qa.question, options))
            question_bm25 = rewritten.Q
            choice_queries = {
                "A": rewritten.A,
                "B": rewritten.B,
                "C": rewritten.C,
                "D": rewritten.D,
            }
        except Exception:
            log.warning("choice-query-rewriter failed, falling back to raw options")
            question_bm25 = qa.question
            choice_queries = options

        # (label, bm25_query, embed_text, rerank_query)
        # QUESTION: BM25=keyword query; embed+rerank both use full question text.
        # CHOICE:   BM25=keyword query; embed+rerank both use full option text.
        queries: list[tuple[str, str, str, str]] = [
            ("QUESTION", question_bm25, qa.question, qa.question)
        ] + [
            (
                f"CHOICE {k}: {choice_queries.get(k, '')}",
                choice_queries.get(k, ""),
                options.get(k, ""),
                options.get(k, ""),  # full option text for reranker
            )
            for k in ("A", "B", "C", "D")
            if k in options
        ]

        schema = f"task_{task_pk}__{run_tag}"
        results = []
        for label, bm25_query, embed_text, rerank_query in queries:
            if not bm25_query:
                continue
            with client.session_factory() as db:
                db.execute(sa_text(f"SET LOCAL search_path TO {schema}, public"))
                path_a, path_b, path_c, reranked, _ = self._run_per_query_debug(
                    bm25_query,
                    task_pk,
                    db,
                    client.embed_client,
                    embed_text,
                    rerank_query,
                )
            results.append((label, bm25_query, path_a, path_b, path_c, reranked))
        return results

    def _retrieve_per_query(
        self,
        client: "LocalSearchRunner",
        task_pk: int,
        qa: "QAPairModel",
        run_tag: str,
    ) -> tuple[list[tuple[str, list[RetrievedFact]]], list[RetrievedFact]]:
        """Core MCQ retrieval logic.

        Returns:
            per_query_facts: [(label, top_pos_facts)] for 5 queries
            global_dnu:      deduplicated DNU facts collected across all queries
        """
        options: dict = json.loads(qa.options) if qa.options else {}

        try:
            rewritten = asyncio.run(self._async_rewrite_choices(qa.question, options))
            question_bm25 = rewritten.Q
            choice_queries = {
                "A": rewritten.A,
                "B": rewritten.B,
                "C": rewritten.C,
                "D": rewritten.D,
            }
        except Exception:
            log.warning("choice-query-rewriter failed, falling back to raw options")
            question_bm25 = qa.question
            choice_queries = options

        # (label, bm25_query, embed_text, rerank_query)
        # QUESTION: BM25=keyword query; embed+rerank both use full question text.
        # CHOICE:   BM25=keyword query; embed+rerank both use full option text.
        queries: list[tuple[str, str, str, str]] = [
            ("QUESTION", question_bm25, qa.question, qa.question)
        ] + [
            (
                f"CHOICE {k}: {choice_queries.get(k, '')}",
                choice_queries.get(k, ""),
                options.get(k, ""),
                options.get(k, ""),  # full option text for reranker
            )
            for k in ("A", "B", "C", "D")
            if k in options
        ]

        schema = f"task_{task_pk}__{run_tag}"
        per_query_facts: list[tuple[str, list[RetrievedFact]]] = []
        dnu_seen: dict[int, RetrievedFact] = {}  # global DNU dedup by fact_id

        for label, bm25_query, embed_text, rerank_query in queries:
            if not bm25_query:
                continue
            with client.session_factory() as db:
                db.execute(sa_text(f"SET LOCAL search_path TO {schema}, public"))
                pos_facts, dnu_pool = self._run_per_query(
                    bm25_query,
                    task_pk,
                    db,
                    client.embed_client,
                    embed_text,
                    rerank_query,
                )
            per_query_facts.append((label, pos_facts))
            for f in dnu_pool:
                if f.fact_id not in dnu_seen:
                    dnu_seen[f.fact_id] = f

        return per_query_facts, list(dnu_seen.values())
