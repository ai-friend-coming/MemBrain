"""Shared message formatting and batching helpers for application workflows."""

from __future__ import annotations

import re

from membrain.config import settings
from membrain.infra.retrieval.candidate_retrieval import EntityContext


def session_time_label(session_time: str) -> str:
    """Return session time formatted to its available precision."""

    value = session_time.strip()
    if len(value) > 10 and re.search(r"\d{1,2}:\d{2}", value[8:]):
        return value
    return value[:10]


def format_lines(messages: list[dict]) -> str:
    """Render messages as ``[ISO time] Speaker: content`` lines."""

    lines: list[str] = []
    for message in messages:
        timestamp = (
            f"[{message['message_time']}] " if message.get("message_time") else ""
        )
        speaker = message["speaker"]
        prefix = f"{timestamp}{speaker}: " if speaker else timestamp
        lines.append(f"{prefix}{message['content']}")
    return "\n".join(lines)


def chunk_messages(
    messages: list[dict],
    max_msgs: int = settings.EXTRACT_BATCH_MAX_MESSAGES,
    max_chars: int = settings.EXTRACT_BATCH_MAX_CHARS,
) -> list[list[dict]]:
    """Split messages into chunks by count and char limits."""

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for message in messages:
        message_chars = len(message["content"])
        if current and (
            len(current) >= max_msgs or current_chars + message_chars > max_chars
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(message)
        current_chars += message_chars
    if current:
        chunks.append(current)
    return chunks


def split_into_batches(messages: list[dict]) -> list[list[dict]]:
    """Split a session into extraction batches."""

    return chunk_messages(
        messages,
        max_msgs=settings.EXTRACT_BATCH_MAX_MESSAGES,
        max_chars=settings.EXTRACT_BATCH_MAX_CHARS,
    )


def build_known_entities_text(entity_context: list[EntityContext]) -> str:
    """Render entity context as a plain list for the Known Entities prompt."""

    if not entity_context:
        return ""

    lines: list[str] = []
    for entity in entity_context:
        parts: list[str] = [f"- {entity.canonical_ref}"]
        if entity.aliases:
            parts.append(f"also known as: {', '.join(entity.aliases)}")
        if entity.desc:
            parts.append(f"desc: {entity.desc}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)
