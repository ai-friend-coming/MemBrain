"""LLM-based BM25 query generation: question → Tantivy query string."""

from __future__ import annotations

import json
import logging
import re

import httpx

from membrain.config import settings

log = logging.getLogger(__name__)

_SYSTEM = """\
You are a search query generator for a BM25 full-text index on personal memory records.

# Task

Given a question about someone's life, generate a single Tantivy query string that will find relevant memory records in a BM25 index.

# Tantivy Query Syntax

- `+term` — MUST appear (AND). Use for the primary entity and essential keywords.
- `term` — SHOULD appear (OR). More matches = higher BM25 score.
- `(term1 term2 term3)` — Grouping. Terms inside are OR'd together.
- Combine freely: `+entity (synonym1 synonym2 synonym3)`

# Index Details

- The index uses English stemming (`pdb.simple` with `stemmer=english`).
- Tokens are lowercased and stemmed (e.g., "swimming" → "swim", "children" → "child").
- Use base/root word forms to match stemmed tokens.

# Query Construction Strategy

1. **Mark core entities with `+`** — the person or object the question is about MUST appear.
2. **Expand with synonyms and related terms** — add words that would plausibly appear in a memory record containing the answer:
   - Quantity questions (How many) → include number words: `one two three four five six seven eight nine ten`
   - Family/relationship terms → expand: `child kid son daughter`, `husband spouse partner marry`, `wife spouse partner`
   - Activity/occupation → expand: `job work career employ office`, `hobby interest enjoy`
   - Location questions → include location-related words: `live move house city street address`
   - Temporal questions → include time markers: `start begin since`, `finish end stop`
3. **Use base verb forms** — `swim` not `swimming`, `read` not `reading`, `go` not `went`.
4. **Include 6-12 terms total** — enough for broad recall without noise.

# Output Format

Output ONLY valid JSON. No explanation. Same language as the question.

```json
{"query": "+entity (term1 term2 term3 ...)"}
```

# Examples

Q: "How many children does Emily have?"
{"query": "+emily (child kid son daughter one two three four five)"}

Q: "Where did Max hide his bone?"
{"query": "+max +bone (hide bury slipper couch under secret spot)"}

Q: "What is Sarah's job?"
{"query": "+sarah (job work career employ office profess company)"}

Q: "When did Jake go on a road trip?"
{"query": "+jake +road +trip (drive car highway motel camp travel)"}

Q: "Who is Rachel's husband?"
{"query": "+rachel (husband spouse partner marry wedding ring)"}

Q: "What books has David read recently?"
{"query": "+david (book read novel author literature story recent)"}

Q: "What color did Anna paint her kitchen?"
{"query": "+anna +kitchen (paint color wall red blue green white yellow)"}

Q: "Does Tom have any pets?"
{"query": "+tom (pet dog cat animal puppy kitten fish bird)"}\
"""

# Allow + ( ) for Tantivy boolean syntax; strip everything else dangerous.
_TANTIVY_UNSAFE = re.compile(r"""[&|!{}\[\]^"~*?:\\/'.,$;`]""")


def _sanitize_tantivy_query(raw: str) -> str:
    """Remove unsafe Tantivy chars while preserving + and ()."""
    cleaned = _TANTIVY_UNSAFE.sub(" ", raw)
    return " ".join(cleaned.split())


def generate_bm25_query(
    question: str,
    http_client: httpx.Client,
    model: str = "",
) -> str:
    """Generate a single Tantivy query string from a user question.

    Returns a sanitized query string suitable for ``pdb.parse()``,
    or an empty string on any failure.
    """
    m = model or settings.QA_LLM_MODEL
    try:
        resp = http_client.post(
            f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
            json={
                "model": m,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": f'Q: "{question}"'},
                ],
                "max_tokens": 200,
                "temperature": 0.0,
            },
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        query = data.get("query", "")
        if isinstance(query, str) and query:
            return _sanitize_tantivy_query(query)
    except Exception:
        log.debug("BM25 query generation failed", exc_info=True)
    return ""
