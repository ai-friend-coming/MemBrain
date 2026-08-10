"""验证 API trace 汇总与同步 digest 失败状态。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from membrain.api.routes import memory as memory_route
from membrain.api.trace import RequestTrace


class _FakeQuery:
    """返回固定待处理会话，满足 `_run_digest` 的最小查询接口。"""

    def __init__(self, sessions: list[SimpleNamespace]) -> None:
        self.sessions = sessions

    def filter(self, *args) -> _FakeQuery:
        return self

    def order_by(self, *args) -> _FakeQuery:
        return self

    def all(self) -> list[SimpleNamespace]:
        return self.sessions


class _FakeDb:
    """提供待处理会话查询和 expunge 接口。"""

    def __init__(self, sessions: list[SimpleNamespace]) -> None:
        self.sessions = sessions

    def query(self, model) -> _FakeQuery:
        return _FakeQuery(self.sessions)

    def expunge(self, session: SimpleNamespace) -> None:
        return None


class _FakeDbContext:
    """将固定数据库对象包装为上下文管理器。"""

    def __init__(self, db: _FakeDb) -> None:
        self.db = db

    def __enter__(self) -> _FakeDb:
        return self.db

    def __exit__(self, *args) -> None:
        return None


class _FakeLock:
    """提供 `_run_digest` 需要的异步锁协议。"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args) -> None:
        return None


class _FailingWorkflow:
    """模拟记忆处理阶段发生不可恢复异常。"""

    async def process_session(self, **kwargs) -> None:
        raise RuntimeError("digest failed")


class _FakeTaskManager:
    """提供固定 workflow 并记录资源清理。"""

    def __init__(self) -> None:
        self.cleaned: list[int] = []

    def get_lock(self, task_pk: int) -> _FakeLock:
        return _FakeLock()

    def get_or_create(self, task_pk: int) -> _FailingWorkflow:
        return _FailingWorkflow()

    def cleanup(self, task_pk: int) -> None:
        self.cleaned.append(task_pk)


class RequestTraceTest(unittest.TestCase):
    """验证 trace usage 和已知模型成本汇总。"""

    def test_snapshot_aggregates_usage_and_cost(self) -> None:
        """汇总 LLM 与 embedding usage，并计算已知模型成本。"""
        trace = RequestTrace()
        trace.add_call(
            kind="llm",
            model="gpt-4.1-mini",
            url="https://example.test/chat/completions",
            started_at=trace.started_at,
            status=200,
            usage={"prompt_tokens": 1000, "completion_tokens": 100},
        )
        trace.add_call(
            kind="embedding",
            model="text-embedding-3-large",
            url="https://example.test/embeddings",
            started_at=trace.started_at,
            status=200,
            usage={"prompt_tokens": 500, "total_tokens": 500},
        )

        snapshot = trace.snapshot()

        self.assertEqual(
            snapshot["total_usage"],
            {"prompt_tokens": 1500, "completion_tokens": 100, "total_tokens": 1600},
        )
        self.assertEqual(snapshot["estimated_cost_usd"], 0.000625)

    def test_persistent_trace_accumulates_calls_across_retry(self) -> None:
        """从旧 trace 继续累计，并在每次新调用后输出持久化快照。"""
        snapshots: list[dict] = []
        trace = RequestTrace(
            initial_trace={
                "duration_ms": 12,
                "calls": [
                    {
                        "kind": "llm",
                        "model": "gpt-4.1-mini",
                        "url": "https://example.test/chat/completions",
                        "duration_ms": 10,
                        "status": 200,
                        "usage": {
                            "prompt_tokens": 2,
                            "completion_tokens": 1,
                            "total_tokens": 3,
                        },
                        "estimated_cost_usd": 0.0,
                        "error": None,
                    }
                ],
            },
            on_change=snapshots.append,
        )
        trace.add_call(
            kind="embedding",
            model="text-embedding-3-large",
            url="https://example.test/embeddings",
            started_at=trace.started_at,
            status=200,
            usage={"prompt_tokens": 4, "total_tokens": 4},
        )

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(snapshots[0]["calls"]), 2)
        self.assertEqual(snapshots[0]["total_usage"]["total_tokens"], 7)
        self.assertGreaterEqual(snapshots[0]["duration_ms"], 12)


class DigestFailureTest(unittest.IsolatedAsyncioTestCase):
    """验证同步 digest 不会把处理异常误报为成功。"""

    async def test_run_digest_returns_failure_after_workflow_error(self) -> None:
        """在 workflow 异常时返回失败标记并清理任务资源。"""
        session = SimpleNamespace(id=42, session_number=1, session_time_raw="")
        manager = _FakeTaskManager()
        db_context = _FakeDbContext(_FakeDb([session]))

        with (
            patch.object(memory_route, "SessionLocal", return_value=db_context),
            patch.object(memory_route, "task_mgr", manager),
            patch.object(
                memory_route,
                "_load_session_messages",
                return_value=[{"speaker": "user", "content": "hello"}],
            ),
        ):
            result = await memory_route._run_digest(7, None)

        self.assertEqual(result, (0, "RuntimeError: digest failed"))
        self.assertEqual(manager.cleaned, [7])
