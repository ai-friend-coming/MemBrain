"""HTTP client for MemBrain backend integration."""

import logging
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


class MembrainClient:
    """Wraps MemBrain backend HTTP calls.

    When ``base_url`` is *None* every method is a silent no-op so the demo
    works without a running MemBrain instance.
    """

    def __init__(
        self,
        base_url: str | None,
        timeout: float = 5.0,
        push_timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._timeout = timeout
        self._push_timeout = push_timeout
        self._http: httpx.AsyncClient | None = (
            httpx.AsyncClient(timeout=timeout) if base_url else None
        )

    async def close(self) -> None:
        """Shut down the underlying HTTP connection pool."""
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── push ────────────────────────────────────────────────────────

    async def push_conversation(
        self,
        owner_id: str,
        persona_id: str,
        messages: list[dict],
        user_alias: str,
        character_name: str,
    ) -> bool:
        """POST /api/memory — fire-and-forget, never raises.

        Returns True on success, False on failure.
        """
        if self._base_url is None:
            return True

        mb_messages = [
            {
                "speaker": user_alias if m["role"] == "user" else character_name,
                "content": m["content"],
                "message_time": (
                    m["created_at"].isoformat()
                    if hasattr(m["created_at"], "isoformat")
                    else str(m["created_at"])
                ),
            }
            for m in messages
        ]

        payload = {
            "dataset": f"user_{owner_id}",
            "task": f"persona_{persona_id}",
            "messages": mb_messages,
            "store": True,
            "digest": True,
        }

        try:
            resp = await self._http.post(
                f"{self._base_url}/api/memory",
                json=payload,
                timeout=self._push_timeout,
            )
            resp.raise_for_status()
            logger.info("MEMBRAIN_PUSH persona=%s status=%s", persona_id, resp.status_code)
            return True
        except Exception:
            logger.warning("MEMBRAIN_PUSH failed persona=%s", persona_id, exc_info=True)
            return False

    # ── search ──────────────────────────────────────────────────────

    async def search_memory(
        self,
        owner_id: str,
        persona_id: str,
        question: str,
        mode: Literal["direct", "expand", "reflect"] = "expand",
        strategy: Literal["rrf", "rerank"] = "rrf",
    ) -> str:
        """POST /api/memory/search — returns packed_context or '' on any failure."""
        if self._base_url is None:
            return ""

        payload = {
            "dataset": f"user_{owner_id}",
            "task": f"persona_{persona_id}",
            "question": question,
            "mode": mode,
            "strategy": strategy,
        }

        try:
            resp = await self._http.post(f"{self._base_url}/api/memory/search", json=payload)
            if resp.status_code == 404:
                logger.info("MEMBRAIN_SEARCH persona=%s task not found", persona_id)
                return ""
            resp.raise_for_status()
            data = resp.json()
            context = data.get("packed_context", "")
            logger.info(
                "MEMBRAIN_SEARCH persona=%s tokens=%s",
                persona_id,
                data.get("packed_token_count", "?"),
            )
            return context
        except Exception:
            logger.warning("MEMBRAIN_SEARCH failed persona=%s", persona_id, exc_info=True)
            return ""
