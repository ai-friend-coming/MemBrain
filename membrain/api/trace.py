"""收集单次 API 请求中的上游调用明细，不产生持久化副作用。"""

from __future__ import annotations

import contextvars
import time
from typing import Any

# 价格按美元/百万 token 估算；未知模型只返回 usage，不虚构成本。
_MODEL_PRICES = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "text-embedding-3-large": (0.13, 0.00),
}

_CURRENT_TRACE: contextvars.ContextVar["RequestTrace | None"] = contextvars.ContextVar(
    "membrain_request_trace", default=None
)


def _as_dict(value: Any) -> dict[str, Any]:
    """将 SDK usage 或 JSON usage 转成普通字典。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    return {}


def normalize_usage(value: Any) -> dict[str, Any]:
    """保留上游 usage 明细，并统一输入、输出和总 token 字段。"""
    raw = _as_dict(value)
    prompt = raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0
    completion = raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0
    total = raw.get("total_tokens", prompt + completion) or 0
    raw.update(
        {
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(total),
        }
    )
    return raw


def response_usage(response: Any) -> dict[str, Any]:
    """从 HTTP 或 SDK 响应中读取 usage，读取失败时返回零值。"""
    usage = getattr(response, "usage", None)
    if usage is None:
        try:
            usage = response.json().get("usage")
        except Exception:
            usage = None
    return normalize_usage(usage)


def _estimate_cost(model: str | None, usage: dict[str, Any]) -> float | None:
    """按已配置的模型单价估算 LLM 调用成本。"""
    if not model or model not in _MODEL_PRICES:
        return None
    input_price, output_price = _MODEL_PRICES[model]
    return round(
        (usage["prompt_tokens"] * input_price + usage["completion_tokens"] * output_price)
        / 1_000_000,
        8,
    )


class RequestTrace:
    """记录一次请求的上游调用、总 usage、耗时和估算成本。"""

    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.calls: list[dict[str, Any]] = []

    def add_call(
        self,
        *,
        kind: str,
        model: str | None,
        url: str | None,
        started_at: float,
        status: int | None = None,
        usage: Any = None,
        error: str | None = None,
    ) -> None:
        """追加一个上游调用记录，并保留失败调用以便定位链路问题。"""
        normalized = normalize_usage(usage)
        self.calls.append(
            {
                "kind": kind,
                "model": model,
                "url": url,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
                "status": status,
                "usage": normalized,
                "estimated_cost_usd": _estimate_cost(model, normalized),
                "error": error,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        """生成可直接放入 API response 的 trace 快照。"""
        total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        cost = 0.0
        has_cost = False
        for call in self.calls:
            usage = call["usage"]
            for key in total:
                total[key] += usage.get(key, 0)
            if call["estimated_cost_usd"] is not None:
                cost += call["estimated_cost_usd"]
                has_cost = True
        return {
            "duration_ms": round((time.perf_counter() - self.started_at) * 1000, 3),
            "calls": self.calls,
            "total_usage": total,
            "estimated_cost_usd": round(cost, 8) if has_cost else None,
        }


def current_trace() -> RequestTrace | None:
    """返回当前请求上下文的 trace collector。"""
    return _CURRENT_TRACE.get()


def start_trace() -> tuple[RequestTrace, contextvars.Token]:
    """创建并绑定当前请求 trace。"""
    trace = RequestTrace()
    return trace, _CURRENT_TRACE.set(trace)


def finish_trace(token: contextvars.Token) -> None:
    """解除当前请求 trace 绑定，避免污染后续请求。"""
    _CURRENT_TRACE.reset(token)


def record_call(**kwargs: Any) -> None:
    """在存在请求上下文时记录上游调用，否则保持调用方原有行为。"""
    trace = current_trace()
    if trace is not None:
        trace.add_call(**kwargs)


def trace_http_post(client: Any, url: str, *, kind: str, model: str | None, **kwargs: Any) -> Any:
    """调用 HTTP 上游并记录响应状态、usage 和耗时。"""
    started_at = time.perf_counter()
    try:
        response = client.post(url, **kwargs)
    except Exception as exc:
        record_call(
            kind=kind,
            model=model,
            url=url,
            started_at=started_at,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_call(
        kind=kind,
        model=model,
        url=url,
        started_at=started_at,
        status=getattr(response, "status_code", None),
        usage=response_usage(response),
        error=(
            f"HTTP {response.status_code}"
            if getattr(response, "status_code", 200) >= 400
            else None
        ),
    )
    return response
