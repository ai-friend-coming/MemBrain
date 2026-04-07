"""Streaming chat endpoint — SSE using AI SDK UIMessageChunks v1 protocol."""

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from ..config import settings
from ..database import get_db
from ..metrics import AGENT_LATENCY, AgentTimer
from ..models import ChatRequest
from ..services import storage
from ..services.formatting import (
    build_pydantic_history,
    compute_trunk_window,
    extract_text,
    format_judge_prompt,
    render_response,
    wrap_history_with_prompts,
)
from ..services.membrain_client import MembrainClient
from ..services.reflection_service import load_reflections, trigger_reflection_if_due

logger = logging.getLogger(__name__)

router = APIRouter()


async def _run_memory_router(af, prior_msgs, user_text, session_id):
    """Memory router — fail-open (exceptions logged, never raised).

    Returns the router output object on success, or None on failure.
    """
    try:
        router_history_msgs = settings.ROUTER_CONTEXT_WINDOW * 2
        trunk = settings.CONTEXT_TRUNK_SIZE * 2
        router_limit = compute_trunk_window(
            len(prior_msgs),
            router_history_msgs,
            trunk,
        )
        router_prior = prior_msgs[-router_limit:] if prior_msgs else []
        router_judge = format_judge_prompt(router_prior + [{"role": "user", "content": user_text}])

        router_agent, router_model_settings = af.get_agent("memory-router")
        router_prompt = af.registry.render_prompts("memory-router")[0]

        t0 = time.perf_counter()
        router_result = await router_agent.run(
            router_judge,
            model_settings=router_model_settings,
            instructions=router_prompt,
        )
        latency = time.perf_counter() - t0
        AGENT_LATENCY.labels(task_id="memory-router").observe(latency)

        router_out_obj = router_result.output
        logger.info(
            "memory-router  session=%s  result=%s  latency=%.3fs",
            session_id,
            router_out_obj.result,
            latency,
        )
        return router_out_obj
    except Exception:
        logger.exception("Memory router failed (continuing)")
        return None


@router.post("/chat")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    persona_id: str = Header(..., alias="X-Persona-ID"),
    x_llm_api_url: str | None = Header(None, alias="X-LLM-API-URL"),
    x_llm_api_key: str | None = Header(None, alias="X-LLM-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream a reply from the LLM using the Vercel AI SDK UIMessageChunks protocol."""

    session_id = body.id or str(uuid7())

    # Priority: persona key > user global key (from header) > env key
    persona = await storage.get_persona(db, persona_id)
    persona_key = persona.llm_api_key if persona else None
    persona_url = persona.llm_api_url if persona else None

    effective_key = persona_key or x_llm_api_key or settings.LLM_API_KEY
    effective_url = persona_url or x_llm_api_url or settings.LLM_API_URL

    from ..registry import AgentFactory

    if effective_key != settings.LLM_API_KEY or effective_url != settings.LLM_API_URL:
        af = AgentFactory(request.app.state.agent_factory.registry, effective_url, effective_key)
    else:
        af = request.app.state.agent_factory

    # ── Stage 1 (parallel DB I/O): Load conversation context + reflections ──
    user_text = extract_text(body.messages[-1].parts)
    max_msgs = settings.RESPONSE_CONTEXT_WINDOW * 2
    trunk = settings.CONTEXT_TRUNK_SIZE * 2

    total_msgs, raw_messages, (char_reflection, user_reflection) = await asyncio.gather(
        storage.count_session_messages(session_id),
        storage.get_recent_messages(session_id, max_msgs),
        load_reflections(persona_id),
    )

    # Trunk-based sliding: drop messages in chunks instead of one-by-one
    fetch_limit = compute_trunk_window(total_msgs, max_msgs, trunk)
    prior_messages = (
        raw_messages[-fetch_limit:] if fetch_limit < len(raw_messages) else raw_messages
    )
    history = build_pydantic_history(prior_messages)

    user_msg_id = str(uuid7())
    assistant_msg_id = str(uuid7())
    part_id = str(uuid7())
    user_created_at = datetime.now(UTC)

    request_start = time.perf_counter()

    # ── Stage 2: Launch router (non-blocking) + prepare speculative fast path ──
    router_task = asyncio.create_task(_run_memory_router(af, prior_messages, user_text, session_id))

    agent, model_settings = af.get_agent("chat-reply")
    deps = {
        "character_name": body.characterName,
        "character_biography": body.characterBiography,
        "user_alias": body.userAlias,
    }

    fast_prompts = [
        p
        for p in af.registry.render_prompts(
            "chat-reply",
            memory_context="",
            character_reflection=char_reflection or "",
            user_reflection=user_reflection or "",
            **deps,
        )
        if p.strip()
    ]
    fast_system = fast_prompts[0] if fast_prompts else ""
    fast_memory = fast_prompts[1] if len(fast_prompts) > 1 else ""
    fast_history = wrap_history_with_prompts(history, fast_system, fast_memory)

    # ── Stage 3 (streaming): Speculative parallel execution ──
    #   ├─ [PARALLEL]  Save user msg ‖ Speculative chat-reply ‖ Router
    #   ├─ [BRANCH]    fast_think → use speculative result
    #   │               deep_think → cancel speculative, retrieve, re-run
    #   ├─ [AWAIT]     Wait for user msg save
    #   ├─ [SEQUENTIAL] Save assistant msg + COMMIT
    #   └─ [FIRE-AND-FORGET] Reflection update
    async def generate():
        full_text = ""
        save_task: asyncio.Task | None = None
        speculative_task: asyncio.Task | None = None
        error_occurred = False
        timer = AgentTimer(task_id="chat-reply")

        try:
            yield f"data: {json.dumps({'type': 'start', 'messageId': assistant_msg_id})}\n\n"
            yield f"data: {json.dumps({'type': 'text-start', 'id': part_id})}\n\n"

            async def _save_user_message() -> None:
                await storage.ensure_session(db, persona_id, session_id)
                await storage.add_messages(
                    db,
                    persona_id,
                    session_id,
                    [
                        {
                            "id": user_msg_id,
                            "role": "user",
                            "content": user_text,
                            "created_at": user_created_at,
                        }
                    ],
                )

            save_task = asyncio.create_task(_save_user_message())

            timer.start()

            # Launch speculative chat-reply assuming fast_think
            speculative_task = asyncio.create_task(
                agent.run(
                    user_text,
                    message_history=fast_history,
                    model_settings=model_settings,
                    deps=deps,
                )
            )

            # Await router (may already be done — nano/mini is fast)
            router_output = await router_task

            use_deep = router_output is not None and router_output.result == "deep_think"

            if use_deep:
                # Cancel speculative call, retrieve memories, re-run
                speculative_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await speculative_task
                speculative_task = None

                membrain: MembrainClient = request.app.state.membrain
                search_question = (
                    router_output.vector_query
                    if router_output and router_output.vector_query
                    else user_text
                )
                memory_context = await membrain.search_memory(
                    owner_id=persona.owner_id if persona else "",
                    persona_id=persona_id,
                    question=search_question,
                    mode="expand",
                    strategy="rerank",
                )
                logger.info(
                    "RETRIEVAL session=%s deep_think context_len=%d",
                    session_id,
                    len(memory_context),
                )

                deep_prompts = [
                    p
                    for p in af.registry.render_prompts(
                        "chat-reply",
                        memory_context=memory_context,
                        character_reflection=char_reflection or "",
                        user_reflection=user_reflection or "",
                        **deps,
                    )
                    if p.strip()
                ]
                deep_system = deep_prompts[0] if deep_prompts else ""
                deep_memory = deep_prompts[1] if len(deep_prompts) > 1 else ""
                deep_history = wrap_history_with_prompts(history, deep_system, deep_memory)

                result = await agent.run(
                    user_text,
                    message_history=deep_history,
                    model_settings=model_settings,
                    deps=deps,
                )
            else:
                # Fast path — use speculative result directly
                result = await speculative_task
                speculative_task = None

            full_text = render_response(result.output)
            timer.mark_first_token()
            yield f"data: {json.dumps({'type': 'text-delta', 'id': part_id, 'delta': full_text})}\n\n"

            await save_task
            save_task = None
            timer.report()

        except Exception:
            error_occurred = True
            logger.exception("LLM streaming error")
            error_msg = "Sorry, an error occurred while generating the response."
            if not full_text:
                yield f"data: {json.dumps({'type': 'text-delta', 'id': part_id, 'delta': error_msg})}\n\n"
            full_text = full_text or error_msg

        finally:
            for task in (save_task, speculative_task):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            if save_task is not None:
                await db.rollback()

        yield f"data: {json.dumps({'type': 'text-end', 'id': part_id})}\n\n"
        yield f"data: {json.dumps({'type': 'finish'})}\n\n"
        yield "data: [DONE]\n\n"

        if not error_occurred:
            await storage.add_messages(
                db,
                persona_id,
                session_id,
                [
                    {
                        "id": assistant_msg_id,
                        "role": "assistant",
                        "content": full_text,
                        "created_at": datetime.now(UTC),
                    }
                ],
            )
            await db.commit()
            membrain: MembrainClient = request.app.state.membrain
            asyncio.create_task(trigger_reflection_if_due(session_id, persona_id, af, membrain))

        total_elapsed = time.perf_counter() - request_start
        logger.info("REQUEST_TOTAL session=%s elapsed=%.3fs", session_id, total_elapsed)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )
