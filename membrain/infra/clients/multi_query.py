"""Multi-query generation with temporal expansion for improved retrieval."""

from __future__ import annotations

import json
import logging

import httpx

from membrain.config import settings

log = logging.getLogger(__name__)

_SYSTEM = """\
You are an expert at query reformulation for long-term conversational memory retrieval.
Generate EXACTLY 3 complementary search queries. Each query has a fixed role:

Query 1 — Event-focused (for embedding):
  For temporal questions ("when did X?", "how long ago?"), drop the time aspect
  entirely and focus on the EVENT itself. Ask what happened, not when.
  Example: "When did Caroline have a picnic?" → "What did Caroline do with friends outdoors?"
  For non-temporal questions, write a specific direct question as-is.

Query 2 — HyDE declarative (for embedding):
  Write the sentence that WOULD appear verbatim in a memory record if the answer existed.
  Include the subject name, action, and plausible specific details.
  Example: "Caroline and her friends had a picnic together."
  Example: "Sunflowers represent warmth and happiness to Caroline."

Query 3 — BM25 keyword strip (for keyword search):
  Keep ONLY entity names + core noun/verb base forms. Remove ALL question words,
  articles, auxiliaries (what/when/did/who/how/is/are/the/a/to/do/does/why).
  Example: "When did Caroline have a picnic?" → "Caroline friends picnic"
  Example: "What do sunflowers represent to Caroline?" → "Caroline sunflower meaning"
  Example: "Why did Melanie use colors in her pottery?" → "Melanie pottery colors reason"
  Example: "How many children does Melanie have?" → "Melanie children kids"

Output ONLY valid JSON: {"queries": ["...", "...", "..."]}
No explanation. Exactly 3 queries, each under 25 words, same language as the question.\
"""


def generate_multi_queries(
    question: str,
    http_client: httpx.Client,
    model: str = "",
) -> list[str]:
    """Generate 3 complementary search queries from a question.

    Returns list of 3 queries: [event, hyde, bm25_kw].
    Falls back to an empty list on any error (caller uses the original question).
    """
    if not settings.QA_MULTI_QUERY_ENABLED:
        return []

    m = model or settings.QA_LLM_MODEL
    try:
        resp = http_client.post(
            f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
            json={
                "model": m,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": f"Question: {question}"},
                ],
                "max_tokens": 250,
                "temperature": 0.0,
            },
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        queries = data.get("queries", [])
        if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
            return queries[:3]
        return []
    except Exception:
        log.debug("multi_query generation failed, falling back to empty", exc_info=True)
        return []
