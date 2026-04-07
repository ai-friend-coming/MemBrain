"""LLM-based query rewriting: full question → compact keyword phrase."""

from __future__ import annotations

import httpx

from membrain.config import settings

_SYSTEM = (
    "Extract 3-6 search keywords from the question. "
    "Keep proper nouns exactly as written. "
    "Use base/infinitive verb forms (e.g. 'research' not 'researching'). "
    "Remove question words (what/when/did/who/how/is/are). "
    "Output only the keywords, space-separated, no punctuation."
)

_EXAMPLES = (
    "Q: When did Melanie paint a sunrise? → Melanie paint sunrise\n"
    "Q: What did Caroline research? → Caroline research\n"
    "Q: What is Caroline's identity? → Caroline identity\n"
    "Q: Where did they go for their anniversary dinner? → anniversary dinner location\n"
)


def rewrite_query(
    question: str,
    http_client: httpx.Client,
    model: str = "",
) -> str:
    """Convert question → 3-6 keyword phrase for BM25/embedding retrieval.

    Falls back to the original question on any error.
    """
    m = model or settings.QA_LLM_MODEL
    try:
        resp = http_client.post(
            f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
            json={
                "model": m,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _EXAMPLES + f"Q: {question} →"},
                ],
                "max_tokens": 40,
                "temperature": 0.0,
            },
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        keywords = resp.json()["choices"][0]["message"]["content"].strip()
        return keywords if keywords else question
    except Exception:
        return question
