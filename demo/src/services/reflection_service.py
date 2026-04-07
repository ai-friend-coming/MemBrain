"""Reflection service — per-persona character & user reflection updates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .membrain_client import MembrainClient

import asyncio
import logging

from sqlalchemy import select, update

from ..config import settings
from ..database import db_session
from ..models.db import PersonaModel
from ..registry import AgentFactory
from .formatting import format_conversation
from .storage import count_user_turns_since, get_messages_since

logger = logging.getLogger(__name__)

# Tracks persona IDs with an in-flight reflection to prevent concurrent updates.
_in_flight: set[str] = set()

# prevent GC from collecting fire-and-forget tasks
_background_tasks: set[asyncio.Task] = set()


async def _load_persona(persona_id: str) -> PersonaModel | None:
    """Load persona with reflection fields."""
    async with db_session() as db:
        result = await db.execute(select(PersonaModel).where(PersonaModel.id == persona_id))
        return result.scalar_one_or_none()


async def load_reflections(persona_id: str) -> tuple[str | None, str | None]:
    """Load (character_reflection, user_reflection) for a persona."""
    async with db_session() as db:
        result = await db.execute(
            select(
                PersonaModel.character_reflection,
                PersonaModel.user_reflection,
            ).where(PersonaModel.id == persona_id)
        )
        row = result.first()
        if not row:
            return None, None
        return row[0], row[1]


async def _run_agent(
    task_id: str,
    existing_reflection: str,
    conversation: str,
    agent_factory: AgentFactory,
) -> str:
    """Run a reflection agent and return the updated reflection text."""
    user_message = f"[Current Reflection Document]\n{existing_reflection or '(empty)'}\n\n[New Conversation]\n{conversation}"

    agent, ms = agent_factory.get_agent(task_id)
    prompt = agent_factory.registry.render_prompts(task_id)[0]
    result = await agent.run(user_message, model_settings=ms, instructions=prompt)
    return result.output


async def _push_and_track_cursor(
    membrain: MembrainClient,
    persona: PersonaModel,
    messages: list[dict],
) -> None:
    """Push messages to MemBrain and advance membrain_cursor_at on success."""
    success = await membrain.push_conversation(
        owner_id=persona.owner_id,
        persona_id=persona.id,
        messages=messages,
        user_alias=persona.user_alias,
        character_name=persona.character_name,
    )
    if success and messages:
        up_to = messages[-1]["created_at"]
        async with db_session() as db:
            await db.execute(
                update(PersonaModel)
                .where(PersonaModel.id == persona.id)
                .values(membrain_cursor_at=up_to)
            )
            await db.commit()
        logger.info("MEMBRAIN_CURSOR_UPDATED persona=%s cursor=%s", persona.id, up_to)


async def _do_update(
    persona: PersonaModel,
    messages: list[dict],
    af: AgentFactory,
    membrain: MembrainClient | None = None,
    membrain_messages: list[dict] | None = None,
) -> None:
    """Run both reflection agents and save results + cursor atomically."""
    conversation = format_conversation(messages, persona.user_alias, persona.character_name)

    # Fire-and-forget MemBrain push — uses its own message window (may be larger
    # than the reflection window if previous pushes failed).
    if membrain is not None and membrain_messages:
        task = asyncio.create_task(_push_and_track_cursor(membrain, persona, membrain_messages))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    char_result, user_result = await asyncio.gather(
        _run_agent("reflection-character", persona.character_reflection or "", conversation, af),
        _run_agent("reflection-user", persona.user_reflection or "", conversation, af),
    )

    up_to_at = messages[-1]["created_at"]
    async with db_session() as db:
        await db.execute(
            update(PersonaModel)
            .where(PersonaModel.id == persona.id)
            .values(
                character_reflection=char_result,
                user_reflection=user_result,
                reflection_cursor_at=up_to_at,
            )
        )
        await db.commit()

    logger.info("REFLECTION_UPDATED persona=%s cursor=%s", persona.id, up_to_at)


async def trigger_reflection_if_due(
    session_id: str,
    persona_id: str,
    af: AgentFactory,
    membrain: MembrainClient | None = None,
) -> None:
    """Fire-and-forget entry: trigger reflection once turn threshold is reached.

    Triggers when turns since reflection_cursor_at >= REFLECTION_INTERVAL_TURNS.
    This naturally handles failure recovery: if reflection fails, the cursor doesn't
    advance, so the next turn will also exceed the threshold and retry.
    If a reflection is already in-flight for this persona, the current attempt is
    skipped (the running task will cover the pending messages).
    """
    if persona_id in _in_flight:
        logger.info("REFLECTION_SKIP persona=%s already in-flight", persona_id)
        return

    persona = await _load_persona(persona_id)
    if not persona:
        return

    turns_since_cursor = await count_user_turns_since(persona_id, persona.reflection_cursor_at)
    if turns_since_cursor < settings.REFLECTION_INTERVAL_TURNS:
        return

    messages = await get_messages_since(
        persona_id, persona.reflection_cursor_at, lookback=settings.REFLECTION_LOOKBACK_MSGS
    )
    if not messages:
        return

    # Fetch MemBrain message window separately — may be larger if previous pushes failed
    mb_messages: list[dict] | None = None
    if membrain is not None:
        if persona.membrain_cursor_at != persona.reflection_cursor_at:
            mb_messages = await get_messages_since(
                persona_id, persona.membrain_cursor_at, lookback=0
            )
        else:
            mb_messages = messages

    _in_flight.add(persona_id)
    try:
        for attempt in range(2):
            try:
                await _do_update(persona, messages, af, membrain, mb_messages)
                return
            except Exception:
                logger.exception(
                    "Reflection update attempt %d/2 failed persona=%s", attempt + 1, persona_id
                )
        # Both attempts failed → cursor not updated; next turn will retry
    finally:
        _in_flight.discard(persona_id)
