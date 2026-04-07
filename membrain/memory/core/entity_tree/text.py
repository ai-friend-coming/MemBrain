"""Text rendering helpers for entity-tree workflows."""

from __future__ import annotations

import re

_TIME_TOKEN_RE = re.compile(r"\[([^\[\]:]+)::([^\[\]]+)\]")


def render_fact_text(text: str) -> str:
    """Render inline time tokens to human-readable form before LLM use."""

    return _TIME_TOKEN_RE.sub(r"\1 (\2)", text)
