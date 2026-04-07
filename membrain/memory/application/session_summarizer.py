"""Application-layer session summarization workflow."""

from __future__ import annotations

from membrain.agents.retry import run_agent_with_retry
from membrain.config import settings
from membrain.infra.persistence.session_summary_store import SessionSummaryStore
from membrain.memory.application.message_text import (
    chunk_messages,
    format_lines,
    session_time_label,
)


class SessionSummarizer:
    """Generate and persist session summaries for supported profiles."""

    _SUPPORTED_PROFILES = {"locomo", "personamemv2", "knowmebench", "longmemeval"}

    def __init__(self, summary_store, registry, factory) -> None:
        self._summary_store: SessionSummaryStore = summary_store
        self._registry = registry
        self._factory = factory

    async def summarize_session(
        self,
        task_pk: int,
        session_id: int,
        session_messages: list[dict],
        session_time: str,
        profile: str | None = None,
    ) -> None:
        if profile not in self._SUPPORTED_PROFILES:
            return

        if self._summary_store.exists(task_pk, session_id):
            return

        if profile == "longmemeval":
            await self._summarize_session_chunked(
                task_pk,
                session_id,
                session_messages,
                session_time,
            )
            return

        messages_text = format_lines(session_messages)
        max_chars = settings.SUMMARY_SESSION_MAX_CHARS
        if len(messages_text) > max_chars:
            truncated: list[dict] = []
            char_count = 0
            for message in session_messages:
                line = (
                    f"[{message.get('message_time', '')}] "
                    f"{message['speaker']}: {message['content']}"
                )
                if char_count + len(line) > max_chars:
                    break
                truncated.append(message)
                char_count += len(line)
            session_messages = truncated
            messages_text = format_lines(session_messages)

        agent, model_settings = self._factory.get_agent(
            "session-summarizer",
            profile=profile,
        )
        prompts = self._registry.render_prompts(
            "session-summarizer",
            profile=profile,
            conversation_start_time=session_time,
            conversation=messages_text,
        )
        result = await run_agent_with_retry(
            agent,
            instructions=prompts,
            model_settings=model_settings,
        )

        if profile == "personamemv2":
            parts = result.output
            summary_content = parts.preferences
            if parts.forgotten_by_user:
                bullets = "\n".join(f"- {item}" for item in parts.forgotten_by_user)
                summary_content = f"{summary_content}\n\n[FORGOTTEN_BY_USER]\n{bullets}"
        else:
            summary_content = result.output
            if profile == "locomo" and session_time:
                label = session_time_label(session_time)
                summary_content = f"[{label}] {summary_content}"

        self._summary_store.save(task_pk, session_id, summary_content)

    async def _summarize_session_chunked(
        self,
        task_pk: int,
        session_id: int,
        session_messages: list[dict],
        session_time: str,
    ) -> None:
        profile = "longmemeval"

        threshold = settings.MSG_COMPRESS_THRESHOLD
        for index, message in enumerate(session_messages):
            if len(message["content"]) <= threshold:
                continue

            context_message = (
                session_messages[index - 1]["content"] if index > 0 else ""
            )
            agent, model_settings = self._factory.get_agent(
                "message-compressor",
                profile=profile,
            )
            prompts = self._registry.render_prompts(
                "message-compressor",
                profile=profile,
                target_message=message["content"],
                target_speaker=message["speaker"],
                context_message=context_message,
            )
            result = await run_agent_with_retry(
                agent,
                instructions=prompts,
                model_settings=model_settings,
            )
            message["content"] = result.output

        running_summary = ""
        for chunk in chunk_messages(
            session_messages,
            max_msgs=settings.EXTRACT_BATCH_MAX_MESSAGES,
            max_chars=settings.EXTRACT_BATCH_MAX_CHARS,
        ):
            messages_text = format_lines(chunk)
            agent, model_settings = self._factory.get_agent(
                "session-summarizer",
                profile=profile,
            )
            prompts = self._registry.render_prompts(
                "session-summarizer",
                profile=profile,
                conversation=messages_text,
                conversation_start_time=session_time,
                running_summary=running_summary,
            )
            result = await run_agent_with_retry(
                agent,
                instructions=prompts,
                model_settings=model_settings,
            )
            running_summary = result.output

        self._summary_store.save(task_pk, session_id, running_summary)
