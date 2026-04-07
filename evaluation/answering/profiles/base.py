"""Default evaluation profile (fallback).

Subclass and override ``retrieve()`` / ``generate_answer()`` to customize
per-dataset retrieval and answer generation behaviour.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import httpx

from membrain.config import settings

if TYPE_CHECKING:
    from evaluation.models.qa import QAPairModel
    from evaluation.runtime.local_search import LocalSearchRunner

COT_SYSTEM = """\
You are an intelligent memory assistant answering questions based on structured \
personal memory records organized by person and topic.

# CRITICAL REQUIREMENTS
1. Never omit specific names — use "Amy's colleague Rob", not "a colleague"
2. Always include exact numbers, amounts, prices, percentages, dates, times
3. Preserve frequencies exactly — "every Tuesday and Thursday", not "twice a week"
4. Maintain all proper nouns and entities as they appear in the records
5. When multiple facts describe similar events, use dates to distinguish them
6. Perform logical inference when evidence strongly suggests connections

# RESPONSE FORMAT (you MUST follow this structure)

## Step 1: CANDIDATE MEMORIES
List every memory that could relate to the question — including facts whose dates \
use relative phrases ("last weekend", "yesterday", "next month"). Do NOT skip a \
fact just because it lacks an inline resolved date; include it and resolve its \
date in Step 4.

## Step 2: KEY INFORMATION
Extract all specific details: names, numbers, dates, frequencies, entities.

## Step 3: CROSS-MEMORY LINKING
Identify shared entities across memories and make reasonable inferences:
  • Placeholder → concrete value: If one memory uses an abstract label \
("home country", "a colleague") and another names the specific value \
("Italy", "David"), substitute. Example: "A moved from [home country]" + \
"A grew up speaking Italian / A's family is Italian" → A moved from Italy.
  • Indirect attributes: Properties of a person's close relatives or formative \
objects can imply attributes of the person (origin, background, beliefs, etc.).
  • Collective pronouns: When a fact says "they/we/together", infer the people \
involved from conversational context.

## Step 4: TIME CALCULATION
Inline resolved dates like [2023-05-07] are the event date — treat as-is, do \
not add or subtract. The "known from session on DATE" label is when it was \
discussed, not when it happened.
Use the session date to resolve relative expressions in the same fact:
  • "yesterday" from session 2023-08-25 → event on 2023-08-24
  • "last week" from session 2023-07-06 → week before 2023-07-06
  • "last weekend" from session 2023-07-10 → 2023-07-08 or 2023-07-09
  • "next month" from session 2023-05-20 → June 2023
For facts with only a relative phrase (no inline date): resolve via session date \
and check whether the result matches the question's time range. If it matches, \
include the fact in your answer.
"## Raw Message Evidence" has exact timestamps — prefer it over inferred facts \
when dates conflict.
If multiple similar events exist at different dates, report all of them.

## Step 5: CONTRADICTION CHECK
When two facts conflict on the same attribute, trust the more recent record.

## Step 6: DETAIL VERIFICATION
Confirm all names, locations, numbers, dates, and proper nouns are in your answer.

## Step 7: SUFFICIENCY CHECK
If no single record states the answer directly, synthesize from multiple records. \
Always commit to the most reasonable inference — do not hedge with "not specified" \
or "no record" when any relevant evidence is present.
  • For indirect evidence: if a concept or attribute is implied by related facts, \
state the inference explicitly rather than "not provided".
  • For "Open Domain" questions ("Would…", "What might…", "likely…"): reason \
from behavioral clues (hobbies, habits, spending, relationships) to reach a \
conclusion. Do not refuse because no single fact says it directly.
Only say "I don't have enough information" when no entity, event, or attribute \
in any record is even tangentially related to the question.

## FINAL ANSWER
State the answer directly and concisely first (a name, date, or short phrase). \
Add supporting details after. Do not lead with hedging — commit, then explain.\
"""


def _extract_final_answer(raw: str) -> str:
    """Extract the answer after 'FINAL ANSWER' marker from CoT output."""
    # Match any heading level (# ## ###) or bold (**) before FINAL ANSWER
    pattern = re.compile(
        r"^[#*\s]*FINAL\s+ANSWER[*\s]*:?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(pattern.finditer(raw))
    if matches:
        answer = raw[matches[-1].end() :].strip()
        if answer:
            return answer
    # Fallback: "FINAL ANSWER: text" inline
    m = re.search(r"FINAL\s+ANSWER\s*:\s+(.+)", raw, re.IGNORECASE | re.DOTALL)
    if m:
        answer = m.group(1).strip()
        if answer:
            return answer
    return raw


class BaseEvalProfile:
    """Default evaluation profile (fallback)."""

    SYSTEM_PROMPT: str = COT_SYSTEM
    use_exact_match: bool = False  # set True for MCQ datasets to skip LLM judge

    def __init__(self, ranker: str = "rrf") -> None:
        self.ranker = ranker

    def retrieve(
        self,
        client: "LocalSearchRunner",
        task_pk: int,
        qa: "QAPairModel",
        top_k: int,
        run_tag: str,
    ) -> str:
        """Return packed_context string from a single search call."""
        result = client.search(
            task_pk=task_pk,
            question=qa.question,
            run_tag=run_tag,
            top_k=top_k,
        )
        return result["packed_context"]

    def generate_answer(
        self,
        qa: "QAPairModel",
        context_text: str,
        model: str,
        http_client: httpx.Client,
    ) -> str:
        """Build prompt, call LLM, extract answer."""
        user_msg = (
            f"Memory records:\n{context_text}\n\n"
            f"Question: {qa.question}\n\n"
            "Follow the structured reasoning steps above, "
            "then give your FINAL ANSWER."
        )
        resp = http_client.post(
            f"{settings.LLM_API_URL.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": settings.QA_LLM_COT_MAX_TOKENS,
                "temperature": 0.3,
            },
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        return _extract_final_answer(raw)
