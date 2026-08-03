"""验证 checkpoint 操作完整处理事实来源关系。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from membrain.infra import checkpoint


class _FakeQuery:
    """模拟 copy_run 查询任务主键。"""

    def join(self, *targets):
        return self

    def filter(self, *conditions):
        return self

    def first(self):
        return (17,)


class _FakeResult:
    """模拟 copy_run 查询事实进度。"""

    def first(self):
        return (2, 4)


class _FakeDb:
    """提供 copy_run 所需的最小数据库会话。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def query(self, *columns):
        return _FakeQuery()

    def execute(self, statement):
        return _FakeResult()


class _FakeConnection:
    """记录复制到新 schema 的 SQL。"""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None) -> None:
        self.statements.append(str(statement))

    def commit(self) -> None:
        self.committed = True


class _FakeEngine:
    """返回记录 SQL 的测试连接。"""

    def __init__(self) -> None:
        self.connection = _FakeConnection()
        self.disposed = False

    def connect(self) -> _FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


class CheckpointProvenanceTest(unittest.TestCase):
    """验证来源关系不会在 checkpoint 生命周期中遗漏。"""

    def test_clear_ingestion_tables_includes_fact_sources(self) -> None:
        """清理 Pass-2 数据时同步清理事实来源关系。"""
        calls: list[tuple[list[str], str]] = []

        with patch.object(
            checkpoint,
            "_run",
            side_effect=lambda command, label: calls.append((command, label)),
        ):
            checkpoint.clear_ingestion_tables(17, "default")

        self.assertEqual(len(calls), 1)
        self.assertIn("task_17__default.fact_sources", calls[0][0][-1])

    def test_copy_run_copies_fact_sources_after_facts(self) -> None:
        """复制 run 时在 facts 之后、fact_refs 之前复制来源关系。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            exps_root = Path(temp_dir).resolve()
            task_dir = exps_root / "source" / "task-a"
            task_dir.mkdir(parents=True)
            (exps_root / "source" / "run.meta.json").write_text(
                '{"dataset": "dataset-a"}'
            )
            settings = SimpleNamespace(
                exps_dir_path=exps_root,
                DATABASE_URL="postgresql+psycopg2://postgres:test@localhost/db",
            )
            engine = _FakeEngine()
            commands: list[list[str]] = []

            with (
                patch.object(checkpoint, "settings", settings),
                patch.object(checkpoint, "SessionLocal", return_value=_FakeDb()),
                patch(
                    "membrain.infra.db.create_run_engine",
                    return_value=engine,
                ),
                patch("membrain.infra.db.init_run_schema"),
                patch.object(
                    checkpoint,
                    "_run",
                    side_effect=lambda command, label: commands.append(command),
                ),
                patch.object(checkpoint, "is_task_done_by_id", return_value=False),
            ):
                successes, errors = checkpoint.copy_run("source", "copied")

        self.assertEqual((successes, errors), (1, []))
        inserts = [
            statement
            for statement in engine.connection.statements
            if statement.startswith("INSERT INTO")
            and "entity_tree_nodes" not in statement
        ]
        fact_index = next(
            i for i, statement in enumerate(inserts) if ".facts " in statement
        )
        source_index = next(
            i for i, statement in enumerate(inserts) if ".fact_sources " in statement
        )
        ref_index = next(
            i for i, statement in enumerate(inserts) if ".fact_refs " in statement
        )
        self.assertLess(fact_index, source_index)
        self.assertLess(source_index, ref_index)
        self.assertTrue(engine.connection.committed)
        self.assertTrue(engine.disposed)
        self.assertTrue(any(command[0] == "pg_dump" for command in commands))

    def test_restore_checkpoint_restores_full_schema_dump(self) -> None:
        """恢复 checkpoint 时使用完整 schema dump 保留来源关系。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            exps_root = Path(temp_dir).resolve()
            dump_dir = exps_root / "default" / "task-a" / "ckpts"
            dump_dir.mkdir(parents=True)
            (dump_dir / "checkpoint.dump").write_bytes(b"checkpoint")
            settings = SimpleNamespace(
                exps_dir_path=exps_root,
                DATABASE_URL="postgresql+psycopg2://postgres:test@localhost/db",
            )
            commands: list[list[str]] = []

            with (
                patch.object(checkpoint, "settings", settings),
                patch.object(checkpoint, "_task_id_from_pk", return_value="task-a"),
                patch.object(checkpoint, "_drop_schema_with_retry"),
                patch.object(
                    checkpoint,
                    "_run",
                    side_effect=lambda command, label: commands.append(command),
                ),
            ):
                checkpoint.restore_checkpoint(17, "default")

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], "pg_restore")
        self.assertNotIn("-t", commands[0])


if __name__ == "__main__":
    unittest.main()
