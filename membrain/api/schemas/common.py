"""定义 MemBrain 各 HTTP 能力共用的响应结构。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TraceCallOut(BaseModel):
    """返回单个上游 API 调用的耗时、usage 和错误信息。"""

    kind: str
    model: str | None = None
    url: str | None = None
    duration_ms: float
    status: int | None = None
    usage: dict[str, Any] = {}
    estimated_cost_usd: float | None = None
    error: str | None = None


class TraceOut(BaseModel):
    """返回当前 API 请求的临时链路追踪汇总。"""

    duration_ms: float
    calls: list[TraceCallOut] = []
    total_usage: dict[str, int] = {}
    estimated_cost_usd: float | None = None
