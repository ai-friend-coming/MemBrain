"""Token-budgeted context packing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from membrain.config import settings
from membrain.retrieval.core.types import (
    RetrievedFact,
    RetrievedSession,
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4 + 1


@dataclass
class PackedContext:
    """Budget-packed, hierarchically formatted context for LLM."""

    text: str
    token_count: int
    fact_ids: list[int] = field(default_factory=list)


def budget_pack(
    facts: list[RetrievedFact],
    max_tokens: int = settings.QA_BUDGET_MAX_TOKENS,
) -> PackedContext:
    """Pack facts into token budget as a flat bullet list.

    Facts are sorted by rerank_score for budget selection, then emitted
    in chronological order as plain bullets under ## Additional Facts.
    """
    # Greedy fill by score
    sorted_facts = sorted(facts, key=lambda f: f.rerank_score, reverse=True)
    selected: list[RetrievedFact] = []
    total_tokens = 0

    for fact in sorted_facts:
        line = _format_fact_line(fact)
        line_tokens = estimate_tokens(line)
        if total_tokens + line_tokens > max_tokens:
            continue

        selected.append(fact)
        total_tokens += line_tokens

    if not selected:
        return PackedContext(text="", token_count=0, fact_ids=[])

    # Flat bullet list sorted chronologically
    sorted_selected = sorted(selected, key=_sort_key_time)
    lines = ["## Additional Facts"] + [_format_fact_line(f) for f in sorted_selected]
    text = "\n".join(lines)
    return PackedContext(
        text=text,
        token_count=estimate_tokens(text),
        fact_ids=[f.fact_id for f in sorted_selected],
    )


_RELATIVE_DATE_RE = re.compile(r"\[([^\]:]+)::([^\]]+)\]")
_TIME_INFO_DATES_RE = re.compile(r"\[([^\]]+)\]")


def _resolve_inline_dates(text: str) -> str:
    """Replace [relative_word::DATE] with [DATE] to avoid LLM date arithmetic errors."""
    return _RELATIVE_DATE_RE.sub(r"[\2]", text)


def _clean_time_info(time_info: str) -> str:
    """Extract only the ISO date(s) from time_info, dropping the relative word.

    e.g. 'yesterday [2023-05-07, ]'            → '2023-05-07'
         'last week [2023-06-02, 2023-06-08]'  → '2023-06-02/2023-06-08'
         'now [2023-05-08T13:56:00Z, ]'        → '2023-05-08'
    """
    m = _TIME_INFO_DATES_RE.search(time_info)
    if not m:
        return time_info
    parts = [p.strip().split("T")[0] for p in m.group(1).split(",")]
    parts = [p for p in parts if p]
    return "/".join(parts) if parts else time_info


def _format_fact_line(fact: RetrievedFact) -> str:
    """Format a single fact as a bullet line with resolved absolute dates.

    - Inline [word::DATE] annotations → stripped to [DATE] (event date, unambiguous).
    - time_info only (no inline annotation) → appended as '(known from message on DATE)'
      so the LLM understands this is the conversation timestamp, not the event date.
    """
    has_inline = bool(_RELATIVE_DATE_RE.search(fact.text))
    line = f"- {_resolve_inline_dates(fact.text)}"
    if fact.time_info and not has_inline:
        line += f" (known from message on {_clean_time_info(fact.time_info)})"
    return line


def _sort_key_time(fact: RetrievedFact) -> str:
    """Sort key for chronological ordering within a group."""
    return fact.time_info or "zzz"


# ── Context section formatting ──


def format_session_section(
    sessions: list[RetrievedSession],
    max_tokens: int,
) -> str:
    """Format session summaries as a context section, respecting token budget."""
    if not sessions:
        return ""
    header = "## Relevant Episodes"
    lines = [header]
    budget = max_tokens - estimate_tokens(header)
    for s in sessions:
        entry = f"**{s.subject}**: {s.content}\n---"
        cost = estimate_tokens(entry)
        if budget - cost < 0:
            break
        lines.append(entry)
        budget -= cost
    return "\n\n".join(lines) if len(lines) > 1 else ""
