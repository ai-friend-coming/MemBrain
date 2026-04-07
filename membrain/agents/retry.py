"""Retry wrapper for pydantic-ai agent calls with transient LLM error handling."""

from __future__ import annotations

import asyncio
import contextvars
import json as _json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 522, 524, 529}
_CURRENT_TASK: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_id", default="unknown"
)
_ERRORS_FILE = Path(__file__).resolve().parents[2] / "output" / "api_errors.jsonl"


def _extract_status_code(exc: BaseException) -> int | None:
    """Walk the exception chain to find an HTTP status code.

    Works regardless of how the proxy or SDK formats the error message.
    """
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(getattr(e, "status_code", None), int):
            return e.status_code  # type: ignore[union-attr]
        resp = getattr(e, "response", None)
        if resp is not None and isinstance(getattr(resp, "status_code", None), int):
            return resp.status_code
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    return None


def _extract_retry_after(exc: BaseException) -> float | None:
    """Extract Retry-After header value (seconds) from exception chain."""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        resp = getattr(e, "response", None)
        if resp is not None:
            val = getattr(getattr(resp, "headers", None), "get", lambda _: None)(
                "Retry-After"
            )
            if val:
                try:
                    return float(val)
                except ValueError:
                    pass
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    return None


def set_current_task(task_id: str) -> None:
    """Set the current task label for error attribution (call at the top of process_task)."""
    _CURRENT_TASK.set(task_id)


def _append_api_error(error_msg: str) -> None:
    try:
        _ERRORS_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = _json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "task_id": _CURRENT_TASK.get(),
                "error": error_msg[:400],
            }
        )
        with open(_ERRORS_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


_MAX_CONTENT_FILTER_ATTEMPTS = 10


async def run_agent_with_retry(
    agent, *, instructions, model_settings, deps=None, max_retries=2
):
    """Run a pydantic-ai agent with retry on transient LLM errors.

    - content_filter: up to _MAX_CONTENT_FILTER_ATTEMPTS tries, no sleep (non-deterministic).
    - other retryable errors (5xx, timeout, connection): up to max_retries tries, exponential backoff.
    """
    normal_attempts = 0
    cf_attempts = 0

    while True:
        try:
            return await agent.run(
                None,
                instructions=instructions,
                model_settings=model_settings,
                deps=deps,
            )
        except Exception as e:
            err_str = str(e)
            status = _extract_status_code(e)

            is_cf = any(
                kw in err_str.lower()
                for kw in ("content_filter", "content_management_policy")
            )
            if is_cf:
                cf_attempts += 1
                if cf_attempts < _MAX_CONTENT_FILTER_ATTEMPTS:
                    log.warning(
                        "LLM call content_filter (attempt %d/%d), retrying immediately",
                        cf_attempts,
                        _MAX_CONTENT_FILTER_ATTEMPTS,
                    )
                    continue  # no sleep
                _append_api_error(err_str)
                raise

            normal_attempts += 1
            if status in _RETRYABLE_STATUS_CODES:
                wait = _extract_retry_after(e) if status == 429 else None
                wait = wait or 2**normal_attempts
                retryable = True
            else:
                retryable = any(
                    kw in err_str.lower()
                    for kw in ("connection", "timeout", "timed out")
                )
                wait = 2**normal_attempts
            if retryable and normal_attempts < max_retries:
                log.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.0fs",
                    normal_attempts,
                    max_retries,
                    err_str[:120],
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                _append_api_error(err_str)
                raise
