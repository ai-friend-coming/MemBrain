"""Prometheus metrics and timing utilities for agent streaming responses."""

import logging
import time

from prometheus_client import Histogram

logger = logging.getLogger(__name__)

AGENT_TTFT = Histogram(
    "agent_ttft_seconds",
    "Time to first token for agent streaming responses",
    labelnames=["task_id"],
    buckets=(0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
)

AGENT_LATENCY = Histogram(
    "agent_latency_seconds",
    "Total latency for non-streaming agent calls",
    labelnames=["task_id"],
    buckets=(0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
)


class AgentTimer:
    """Measures Time to First Token (TTFT) for an agent streaming response."""

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self._start: float | None = None
        self._first_token: float | None = None

    def start(self) -> None:
        """Record the start time (call just before agent.run_stream)."""
        self._start = time.perf_counter()

    def mark_first_token(self) -> None:
        """Record the first-token time. Only the first call has any effect."""
        if self._first_token is None:
            self._first_token = time.perf_counter()

    def report(self) -> None:
        """Compute TTFT, observe into the Prometheus histogram, and log it."""
        if self._start is None or self._first_token is None:
            logger.warning(
                "TTFT task_id=%s — incomplete timing (no tokens received?)", self._task_id
            )
            return

        ttft = self._first_token - self._start
        AGENT_TTFT.labels(task_id=self._task_id).observe(ttft)
        logger.info("TTFT task_id=%s ttft=%.3fs", self._task_id, ttft)


class AgentLatencyTimer:
    """Measures total latency for a non-streaming agent.run() call."""

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self._start: float | None = None

    def start(self) -> None:
        self._start = time.perf_counter()

    def report(self) -> None:
        if self._start is None:
            logger.warning("LATENCY task_id=%s — timer never started", self._task_id)
            return
        latency = time.perf_counter() - self._start
        AGENT_LATENCY.labels(task_id=self._task_id).observe(latency)
        logger.info("LATENCY task_id=%s latency=%.3fs", self._task_id, latency)
