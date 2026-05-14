"""Sync embedding client for the embedding service."""

import logging
import time

import httpx

from membrain.config import settings

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 1  # seconds: 1, 2, 4


class EmbeddingClient:
    """Synchronous wrapper around the embedding HTTP API."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.url = base_url or settings.EMBED_SERVICE_URL
        self.model = model or settings.EMBED_MODEL
        key = api_key if api_key is not None else settings.EMBED_API_KEY
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.Client(timeout=timeout, headers=headers)

    def _retry_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """POST/GET with retry + exponential backoff.

        Retryable: network errors, timeouts, 429, 5xx.
        Non-retryable: 4xx client errors (except 429) — raised immediately.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429:
                    retry_after = exc.response.headers.get("Retry-After")
                    delay = (
                        float(retry_after)
                        if retry_after
                        else _BACKOFF_BASE * (2**attempt)
                    )
                elif status >= 500:
                    delay = _BACKOFF_BASE * (2**attempt)
                else:
                    raise  # 4xx client error — not retryable
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    log.warning(
                        "Embedding request failed (attempt %d/%d): HTTP %d — retrying in %.0fs",
                        attempt + 1,
                        _MAX_RETRIES,
                        status,
                        delay,
                    )
                    time.sleep(delay)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _BACKOFF_BASE * (2**attempt)
                    log.warning(
                        "Embedding request failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1,
                        _MAX_RETRIES,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
        log.error(
            "Embedding request failed after %d attempts: %s", _MAX_RETRIES, last_exc
        )
        raise last_exc  # type: ignore[misc]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, returning a list of vectors."""
        if not texts:
            return []
        if settings.EMBED_BACKEND.lower() == "mlx":
            from membrain.infra.clients.mlx_local import mlx_embed

            return mlx_embed(texts)
        resp = self._retry_request(
            "POST",
            self.url,
            json={"model": self.model, "input": texts},
        )
        data = resp.json()
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string."""
        if not text or not text.strip():
            return None  # type: ignore[return-value]
        return self.embed([text])[0]

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
