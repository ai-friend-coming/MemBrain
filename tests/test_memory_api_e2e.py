"""通过真实 MemBrain HTTP API 验证记忆写入与召回联动。"""

from __future__ import annotations

import os
import time
import unittest
import uuid
from collections.abc import Callable

import httpx
import psycopg2

_BASE_URL = os.getenv("MEMBRAIN_E2E_BASE_URL", "")
_DATABASE_URL = os.getenv("MEMBRAIN_E2E_DATABASE_URL", "")
_WAIT_SECONDS = int(os.getenv("MEMBRAIN_E2E_WAIT_SECONDS", "180"))


@unittest.skipUnless(
    _BASE_URL and _DATABASE_URL,
    "需要 MEMBRAIN_E2E_BASE_URL 和 MEMBRAIN_E2E_DATABASE_URL 才能运行真实 API 测试",
)
class MemoryApiE2ETest(unittest.TestCase):
    """验证同步 digest、请求 trace、来源标记与 recall 的真实端到端契约。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建独立的 HTTP 客户端和本轮测试数据集。"""
        # 同步 digest 需要覆盖完整上游链路，沿用 E2E 的统一等待上限。
        cls.client = httpx.Client(base_url=_BASE_URL, timeout=float(_WAIT_SECONDS))
        health = cls.client.get("/health")
        health.raise_for_status()
        cls.db = psycopg2.connect(_DATABASE_URL)
        cls.db.autocommit = True
        cls.dataset = f"e2e_chat_provenance_{uuid.uuid4().hex[:12]}"

    @classmethod
    def tearDownClass(cls) -> None:
        """关闭真实 HTTP 连接池。"""
        cls.client.close()
        cls.db.close()

    def _add(
        self,
        task: str,
        chat_id: str,
        content: str,
        message_time: str,
        agent_profile: str | None = None,
        dataset: str | None = None,
    ) -> dict:
        """调用真实写入接口并返回新建会话信息。

        Args:
            task: 当前测试的记忆任务。
            chat_id: 外部聊天 ID。
            content: 待抽取事实的用户消息。
            message_time: 来源消息的 ISO 8601 时间。
            agent_profile: 可选的抽取与会话摘要 profile。
            dataset: 可选的记忆数据集，默认使用本轮测试数据集。

        Returns:
            dict: `/api/memory` 的 JSON 响应。
        """
        response = self.client.post(
            "/api/memory",
            json={
                "dataset": dataset or self.dataset,
                "task": task,
                "chat_id": chat_id,
                "messages": [
                    {
                        "speaker": "E2EUser",
                        "content": content,
                        "message_time": message_time,
                    }
                ],
                "session_time": message_time,
                "store": True,
                "digest": True,
                "wait_for_digest": True,
                "agent_profile": agent_profile,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self.assertEqual(payload["status"], "stored_and_digested")
        self.assertIsInstance(payload["session_id"], int)
        self.assertGreaterEqual(payload["digested_sessions"], 1)
        self.assertTrue(payload["trace"]["calls"])
        self.assertIn("total_tokens", payload["trace"]["total_usage"])
        with self.db.cursor() as cursor:
            cursor.execute(
                "SELECT digested_at FROM chat_sessions WHERE id = %s",
                (payload["session_id"],),
            )
            self.assertIsNotNone(cursor.fetchone()[0])
        return payload

    def _search(
        self,
        task: str,
        question: str,
        dataset: str | None = None,
    ) -> dict:
        """调用真实召回接口并校验来源字段结构。

        Args:
            task: 待检索的记忆任务。
            question: 召回问题。
            dataset: 可选的记忆数据集，默认使用本轮测试数据集。

        Returns:
            dict: `/api/memory/search` 的 JSON 响应。
        """
        response = self.client.post(
            "/api/memory/search",
            json={
                "dataset": dataset or self.dataset,
                "task": task,
                "question": question,
                "mode": "direct",
                "strategy": "rrf",
                "top_k": 20,
            },
        )
        response.raise_for_status()
        payload = response.json()
        self.assertIn("trace", payload)
        self.assertIn("total_usage", payload["trace"])
        self.assertIsInstance(payload["trace"]["calls"], list)
        for fact in payload["facts"]:
            self.assertIsInstance(fact["source_chat_ids"], list)
            self.assertTrue(fact["source_chat_ids"])
        for session in payload["sessions"]:
            self.assertIsInstance(session["chat_id"], str)
            self.assertTrue(session["chat_id"])
        return payload

    def _wait_for_recall(
        self,
        task: str,
        question: str,
        ready: Callable[[dict], bool],
        expectation: str,
        dataset: str | None = None,
    ) -> dict:
        """轮询召回接口，直到异步 digest 产生预期结果。

        Args:
            task: 待检索的记忆任务。
            question: 召回问题。
            ready: 判断 digest 结果是否就绪的函数。
            expectation: 超时时输出的预期说明。
            dataset: 可选的记忆数据集，默认使用本轮测试数据集。

        Returns:
            dict: 第一个满足条件的召回响应。
        """
        deadline = time.monotonic() + _WAIT_SECONDS
        last_result: dict | None = None
        last_error = ""
        while time.monotonic() < deadline:
            try:
                last_result = self._search(task, question, dataset)
                if ready(last_result):
                    return last_result
            except httpx.HTTPError as exc:
                # digest 尚未创建任务 schema 时，召回可能短暂失败。
                last_error = str(exc)
            time.sleep(2)
        self.fail(
            f"等待真实 recall 超时: {expectation}; "
            f"last_error={last_error!r}; last_result={last_result!r}"
        )

    @staticmethod
    def _facts_with(payload: dict, keyword: str) -> list[dict]:
        """返回文本包含指定关键词的召回事实。

        Args:
            payload: 召回接口的 JSON 响应。
            keyword: 不区分大小写的事实关键词。

        Returns:
            list[dict]: 匹配的事实条目。
        """
        keyword = keyword.lower()
        return [fact for fact in payload["facts"] if keyword in fact["text"].lower()]

    @staticmethod
    def _facts_describing(payload: dict, keyword: str) -> list[dict]:
        """从事实文本及其实体字段中定位包含指定细节的事实。

        Args:
            payload: 召回接口的 JSON 响应。
            keyword: 不区分大小写的事实关键词。

        Returns:
            list[dict]: 文本或实体解析字段包含该关键词的事实条目。
        """
        keyword = keyword.lower()
        return [
            fact
            for fact in payload["facts"]
            if keyword
            in " ".join(
                (
                    fact["text"],
                    fact.get("entity_ref", ""),
                    fact.get("aspect_path", ""),
                )
            ).lower()
        ]

    def test_memory_requires_chat_id(self) -> None:
        """拒绝缺少外部聊天 ID 的真实写入请求。"""
        response = self.client.post(
            "/api/memory",
            json={
                "dataset": self.dataset,
                "task": "required-chat-id",
                "messages": [{"speaker": "E2EUser", "content": "ignored"}],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertTrue(
            any(
                item.get("loc", [])[-1:] == ["chat_id"]
                for item in response.json()["detail"]
            )
        )

        empty_response = self.client.post(
            "/api/memory",
            json={
                "dataset": self.dataset,
                "task": "required-chat-id",
                "chat_id": "",
                "messages": [{"speaker": "E2EUser", "content": "ignored"}],
            },
        )
        self.assertEqual(empty_response.status_code, 422)

    def test_single_chat_add_then_recall(self) -> None:
        """写入单个聊天并召回事实与会话来源。"""
        task = "single-chat"
        self._add(
            task,
            "chat-single",
            "My unique E2E beverage is birch sap.",
            "2026-08-01T09:00:00Z",
            agent_profile="locomo",
        )

        result = self._wait_for_recall(
            task,
            "What is E2EUser's unique beverage?",
            lambda payload: (
                bool(self._facts_with(payload, "birch"))
                and any(
                    session["chat_id"] == "chat-single"
                    for session in payload["sessions"]
                )
            ),
            "事实和会话摘要都带有 chat-single",
        )

        facts = self._facts_with(result, "birch")
        self.assertTrue(facts)
        self.assertTrue(
            all(fact["source_chat_ids"] == ["chat-single"] for fact in facts)
        )

    def test_same_fact_accumulates_chat_ids_and_deduplicates_retry(self) -> None:
        """累计同义事实的多个聊天来源，并防止矛盾事实被合并。"""
        task = "multi-source"
        content = "E2EUser's unique E2E database preference is PostgreSQL."
        self._add(task, "chat-alpha", content, "2026-08-01T10:00:00Z")
        self._wait_for_recall(
            task,
            "Which unique E2E database does E2EUser prefer?",
            lambda payload: bool(self._facts_with(payload, "postgresql")),
            "首个 PostgreSQL 事实已可召回",
        )

        self._add(task, "chat-beta", content, "2026-08-02T10:00:00Z")
        result = self._wait_for_recall(
            task,
            "Which unique E2E database does E2EUser prefer?",
            lambda payload: any(
                set(fact["source_chat_ids"]) == {"chat-alpha", "chat-beta"}
                for fact in self._facts_with(payload, "postgresql")
            ),
            "PostgreSQL 事实累计 chat-alpha 和 chat-beta",
        )
        facts = self._facts_with(result, "postgresql")
        self.assertEqual(len(facts), 1)

        self._add(
            task,
            "chat-gamma",
            "For E2E work, E2EUser uniquely prefers the PostgreSQL database.",
            "2026-08-03T10:00:00Z",
        )
        result = self._wait_for_recall(
            task,
            "Which unique E2E database does E2EUser prefer?",
            lambda payload: any(
                set(fact["source_chat_ids"])
                == {"chat-alpha", "chat-beta", "chat-gamma"}
                for fact in self._facts_with(payload, "postgresql")
            ),
            "PostgreSQL 改写事实累计 chat-gamma",
        )
        self.assertEqual(len(self._facts_with(result, "postgresql")), 1)

        retry = self._add(task, "chat-beta", content, "2026-08-03T10:00:00Z")
        self.assertIsInstance(retry["session_id"], int)
        result = self._wait_for_recall(
            task,
            "Which unique E2E database does E2EUser prefer?",
            lambda payload: bool(self._facts_with(payload, "postgresql")),
            "重试会话完成 digest 后 PostgreSQL 事实仍可召回",
        )
        facts = self._facts_with(result, "postgresql")
        self.assertEqual(len(facts), 1)
        canonical_fact_id = facts[0]["fact_id"]
        self.assertEqual(
            set(facts[0]["source_chat_ids"]),
            {"chat-alpha", "chat-beta", "chat-gamma"},
        )
        self.assertEqual(len(facts[0]["source_chat_ids"]), 3)

        self._add(
            task,
            "chat-conflict",
            "E2EUser's unique E2E database preference is MySQL, not PostgreSQL.",
            "2026-08-04T10:00:00Z",
        )
        result = self._wait_for_recall(
            task,
            "Which unique E2E database does E2EUser prefer?",
            lambda payload: bool(self._facts_with(payload, "mysql")),
            "与 PostgreSQL 冲突的 MySQL 事实独立可召回",
        )
        postgres_facts = self._facts_with(result, "postgresql")
        mysql_facts = self._facts_with(result, "mysql")
        self.assertTrue(postgres_facts)
        self.assertTrue(mysql_facts)
        canonical_fact = next(
            fact for fact in result["facts"] if fact["fact_id"] == canonical_fact_id
        )
        self.assertEqual(
            set(canonical_fact["source_chat_ids"]),
            {"chat-alpha", "chat-beta", "chat-gamma"},
        )
        self.assertTrue(
            all(
                fact["fact_id"] != canonical_fact_id
                and fact["source_chat_ids"] == ["chat-conflict"]
                for fact in mysql_facts
            )
        )

    def test_task_memory_isolation(self) -> None:
        """限制 recall 只返回当前 dataset 和 task 的记忆。"""
        self._add(
            "isolated-a",
            "chat-isolated-a",
            "My E2E isolation token is COBALT-7319.",
            "2026-08-01T11:00:00Z",
        )
        self._add(
            "isolated-b",
            "chat-isolated-b",
            "My E2E control token is AMBER-9281.",
            "2026-08-01T11:01:00Z",
        )
        result_a = self._wait_for_recall(
            "isolated-a",
            "What is the E2E isolation token?",
            lambda payload: bool(self._facts_with(payload, "cobalt-7319")),
            "isolated-a 召回 COBALT-7319",
        )
        result_b = self._wait_for_recall(
            "isolated-b",
            "What is the E2E control token?",
            lambda payload: bool(self._facts_with(payload, "amber-9281")),
            "isolated-b 召回 AMBER-9281",
        )

        self.assertFalse(self._facts_with(result_a, "amber-9281"))
        self.assertFalse(self._facts_with(result_b, "cobalt-7319"))
        self.assertTrue(
            all(
                set(fact["source_chat_ids"]) == {"chat-isolated-a"}
                for fact in result_a["facts"]
            )
        )
        self.assertTrue(
            all(
                set(fact["source_chat_ids"]) == {"chat-isolated-b"}
                for fact in result_b["facts"]
            )
        )

    def test_same_task_isolated_between_datasets(self) -> None:
        """隔离不同 dataset 下同名 task 的事实和来源。"""
        task = "shared-task-name"
        other_dataset = f"{self.dataset}_other"
        self._add(
            task,
            "chat-dataset-a",
            "My unique E2E dataset token is VIOLET-6203.",
            "2026-08-01T11:10:00Z",
        )
        self._add(
            task,
            "chat-dataset-b",
            "My unique E2E dataset token is SCARLET-4178.",
            "2026-08-01T11:11:00Z",
            dataset=other_dataset,
        )

        result_a = self._wait_for_recall(
            task,
            "What is the unique E2E dataset token?",
            lambda payload: bool(self._facts_with(payload, "violet-6203")),
            "默认 dataset 召回 VIOLET-6203",
        )
        result_b = self._wait_for_recall(
            task,
            "What is the unique E2E dataset token?",
            lambda payload: bool(self._facts_with(payload, "scarlet-4178")),
            "另一 dataset 召回 SCARLET-4178",
            dataset=other_dataset,
        )

        self.assertFalse(self._facts_with(result_a, "scarlet-4178"))
        self.assertFalse(self._facts_with(result_b, "violet-6203"))
        self.assertTrue(
            all(
                fact["source_chat_ids"] == ["chat-dataset-a"]
                for fact in result_a["facts"]
            )
        )
        self.assertTrue(
            all(
                fact["source_chat_ids"] == ["chat-dataset-b"]
                for fact in result_b["facts"]
            )
        )

    def test_truth_defining_differences_remain_distinct(self) -> None:
        """保留主体、否定、数量和条件不同的事实。"""
        task = "truth-defining-details"
        self._add(
            task,
            "chat-details-a",
            (
                "Avery's unique E2E access code is AURORA-1001. "
                "Mina explicitly permits the unique E2E signal NOVA-332. "
                "Quinn owns exactly 2 unique E2E badges marked BADGE-801. "
                "Riley uses unique E2E mode MODE-551 only during testing."
            ),
            "2026-08-01T13:00:00Z",
        )
        self._add(
            task,
            "chat-details-b",
            (
                "Blake's unique E2E access code is AURORA-1001. "
                "Mina explicitly does not permit the unique E2E signal NOVA-332. "
                "Quinn owns exactly 3 unique E2E badges marked BADGE-801. "
                "Riley uses unique E2E mode MODE-551 only during production."
            ),
            "2026-08-02T13:00:00Z",
        )

        result = self._wait_for_recall(
            task,
            "Recall every unique E2E access code, signal, badge, and mode detail.",
            lambda payload: all(
                len(self._facts_describing(payload, keyword)) >= 2
                for keyword in ("aurora-1001", "nova-332", "badge-801", "mode-551")
            ),
            "四组语义差异均保留为独立事实",
        )

        for keyword in ("aurora-1001", "nova-332", "badge-801", "mode-551"):
            facts = self._facts_describing(result, keyword)
            self.assertGreaterEqual(len(facts), 2)
            self.assertEqual(
                {tuple(fact["source_chat_ids"]) for fact in facts},
                {("chat-details-a",), ("chat-details-b",)},
            )

        subject_text = " ".join(
            fact["text"].lower()
            for fact in self._facts_describing(result, "aurora-1001")
        )
        self.assertIn("avery", subject_text)
        self.assertIn("blake", subject_text)

        polarity_texts = [
            fact["text"].lower() for fact in self._facts_describing(result, "nova-332")
        ]
        self.assertTrue(any("permit" in text for text in polarity_texts))
        self.assertTrue(
            any(
                marker in text
                for text in polarity_texts
                for marker in ("not permit", "forbid", "reject", "deny")
            )
        )

        quantity_text = " ".join(
            fact["text"].lower() for fact in self._facts_describing(result, "badge-801")
        )
        self.assertTrue("2" in quantity_text or "two" in quantity_text)
        self.assertTrue("3" in quantity_text or "three" in quantity_text)

        condition_text = " ".join(
            fact["text"].lower() for fact in self._facts_describing(result, "mode-551")
        )
        self.assertIn("testing", condition_text)
        self.assertIn("production", condition_text)

    def test_semantic_time_keeps_distinct_facts(self) -> None:
        """保留不同语义日期的同文事实及各自来源。"""
        task = "semantic-time"
        content = "Yesterday, I visited the unique E2E city of Lumenport."
        self._add(task, "chat-date-a", content, "2026-08-01T12:00:00Z")
        self._wait_for_recall(
            task,
            "When did E2EUser visit Lumenport?",
            lambda payload: bool(self._facts_with(payload, "lumenport")),
            "第一条 Lumenport 事实已可召回",
        )

        self._add(task, "chat-date-b", content, "2026-08-02T12:00:00Z")
        result = self._wait_for_recall(
            task,
            "When did E2EUser visit Lumenport?",
            lambda payload: len(self._facts_with(payload, "lumenport")) >= 2,
            "不同语义日期生成两条 Lumenport 事实",
        )

        facts = self._facts_with(result, "lumenport")
        self.assertEqual(
            {chat_id for fact in facts for chat_id in fact["source_chat_ids"]},
            {"chat-date-a", "chat-date-b"},
        )
        self.assertTrue(all(len(fact["source_chat_ids"]) == 1 for fact in facts))
        date_sources = {
            date: {
                chat_id
                for fact in facts
                if date in f"{fact['text']} {fact['time_info']}"
                for chat_id in fact["source_chat_ids"]
            }
            for date in ("2026-07-31", "2026-08-01")
        }
        self.assertEqual(date_sources["2026-07-31"], {"chat-date-a"})
        self.assertEqual(date_sources["2026-08-01"], {"chat-date-b"})


if __name__ == "__main__":
    unittest.main()
