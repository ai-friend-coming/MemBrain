"""Sync rerank client for the rerank service."""

import logging
import time

import httpx

from membrain.config import settings

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 1  # seconds: 1, 2, 4


class RerankClient:
    """Synchronous wrapper around the rerank HTTP API."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        self.url = base_url or settings.RERANK_SERVICE_URL
        self.model = model or settings.RERANK_MODEL
        key = api_key if api_key is not None else settings.RERANK_API_KEY
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
                        "Rerank request failed (attempt %d/%d): HTTP %d — retrying in %.0fs",
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
                        "Rerank request failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1,
                        _MAX_RETRIES,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
        log.error("Rerank request failed after %d attempts: %s", _MAX_RETRIES, last_exc)
        raise last_exc  # type: ignore[misc]

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[dict]:
        """Rerank documents by relevance to query.

        Returns list of {"index": int, "relevance_score": float} sorted desc.
        """
        resp = self._retry_request(
            "POST",
            self.url,
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
        )
        results = resp.json()["results"]
        return sorted(results, key=lambda x: x["relevance_score"], reverse=True)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
