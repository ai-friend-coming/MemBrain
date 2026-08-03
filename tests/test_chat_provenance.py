"""验证外部聊天 ID 在写入与检索结果中的来源链路。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from demo.src.services.membrain_client import MembrainClient
from membrain.api.schemas.memory import MemoryRequest
from membrain.infra.models.dataset import ChatSessionModel
from membrain.infra.models.memory import FactModel, FactRefModel, FactSourceModel
from membrain.infra.persistence.batch_writer import write_batch_results
from membrain.infra.persistence.memory_ingest_store import _match_exact_fact_ids
from membrain.infra.retrieval.fact_retrieval import aggregate_session_scores
from membrain.memory.application.ingest_workflow import DefaultIngestWorkflow
from membrain.memory.core.entity_resolver import ResolverDecision, resolve_entities
from membrain.retrieval.application.retrieval import (
    _inject_source_chat_ids,
    _resolve_pool_entity_refs,
)
from membrain.retrieval.core.types import RetrievedFact


class _FakeQuery:
    """返回测试指定的查询行或内部会话主键。"""

    def __init__(self, rows=None, scalar_value=None) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def filter(self, *conditions):
        return self

    def join(self, *targets):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar_value


class _FakeWriteSession:
    """记录批量持久化对象并模拟数据库生成事实主键。"""

    def __init__(self, session_id: int) -> None:
        self.session_id = session_id
        self.added: list[object] = []
        self._next_fact_id = 101

    def query(self, *columns) -> _FakeQuery:
        if len(columns) == 1 and columns[0] is FactModel:
            return _FakeQuery(
                rows=[item for item in self.added if isinstance(item, FactModel)]
            )
        if columns and columns[0] is ChatSessionModel.id:
            return _FakeQuery(scalar_value=self.session_id)
        if columns and columns[0] is FactSourceModel.fact_id:
            return _FakeQuery(
                rows=[
                    (item.fact_id, item.session_id)
                    for item in self.added
                    if isinstance(item, FactSourceModel)
                ]
            )
        if columns and columns[0] is FactRefModel.fact_id:
            return _FakeQuery(
                rows=[
                    (item.fact_id, item.entity_id, item.alias_text)
                    for item in self.added
                    if isinstance(item, FactRefModel)
                ]
            )
        return _FakeQuery()

    def add_all(self, objects) -> None:
        self.added.extend(objects)

    def execute(self, statement, params) -> None:
        return None

    def flush(self) -> None:
        for item in self.added:
            if isinstance(item, FactModel) and item.id is None:
                item.id = self._next_fact_id
                self._next_fact_id += 1


class _FakeEmbeddingClient:
    """为事实写入提供固定向量，避免测试访问外部服务。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] for _ in texts]


class _FakeRows:
    """模拟 SQLAlchemy 查询结果。"""

    def __init__(self, rows: list[tuple[int, list[str]]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[int, list[str]]]:
        return self._rows


class _FakeReadSession:
    """返回事实对应的聚合聊天 ID。"""

    def execute(self, statement, params) -> _FakeRows:
        return _FakeRows([(1, ["chat-a", "chat-b"]), (2, ["chat-c"])])


class _FakeEntityRefSession:
    """返回按事实隔离的同名实体引用。"""

    def execute(self, statement, params) -> _FakeRows:
        return _FakeRows(
            [
                (1, "Alex", "Alex Chen"),
                (2, "Alex", "Alex Wang"),
            ]
        )


class _FakeAggregateSession:
    """返回同一事实的两个来源会话及其摘要。"""

    def execute(self, statement, params) -> _FakeRows:
        return _FakeRows([(1, 77), (1, 88)])

    def query(self, *columns) -> _FakeQuery:
        return _FakeQuery(
            rows=[
                (501, 77, "会话 A", "内容 A", 3, "chat-a"),
                (502, 88, "会话 B", "内容 B", 4, "chat-b"),
            ]
        )


class _FakeResponse:
    """模拟成功的 MemBrain HTTP 响应。"""

    status_code = 200

    def raise_for_status(self) -> None:
        return None


class _FakeHttpClient:
    """记录 demo 客户端发送的请求载荷。"""

    def __init__(self) -> None:
        self.payload: dict | None = None

    async def post(self, url, json, timeout) -> _FakeResponse:
        self.payload = json
        return _FakeResponse()


class _FakeEquivalenceStore:
    """返回指定的精确命中和语义候选。"""

    def __init__(self, exact_ids: list[int | None], candidates: list[list[dict]]):
        self.exact_ids = exact_ids
        self.candidates = candidates

    def load_fact_equivalence_candidates(
        self,
        facts,
        decisions,
        task_id,
        embed_client,
    ):
        return self.exact_ids, self.candidates


class _CapturingRegistry:
    """记录传给事实 resolver 的结构化比较参数。"""

    def __init__(self) -> None:
        self.comparisons: list[dict] = []

    def render_prompts(self, task_id, profile, comparisons_json):
        self.comparisons = json.loads(comparisons_json)
        return ["fact resolver prompt"]


class _FakeAgentFactory:
    """为 workflow 测试提供无需网络的占位 agent。"""

    def get_agent(self, task_id, profile):
        return object(), None


class ChatProvenanceTest(unittest.TestCase):
    """验证聊天来源契约和关系持久化。"""

    def test_memory_request_requires_chat_id(self) -> None:
        """拒绝未提供 chat_id 的记忆写入请求。"""
        with self.assertRaises(ValidationError):
            MemoryRequest(dataset="user-1", task="persona-1")
        with self.assertRaises(ValidationError):
            MemoryRequest(dataset="user-1", task="persona-1", chat_id="")

    def test_reused_fact_accumulates_source_sessions(self) -> None:
        """复用相同事实并累计两个内部来源会话。"""
        db = _FakeWriteSession(session_id=77)

        for batch_id, session_number, source_session_id in (
            ("batch-1", 3, 77),
            ("batch-2", 4, 88),
            ("batch-3", 4, 88),
        ):
            db.session_id = source_session_id
            fact = {"text": "用户喜欢咖啡", "fact_ts": "2026-08-02"}
            if batch_id != "batch-1":
                fact["_equivalent_fact_id"] = 101
            write_batch_results(
                db=db,
                task_id=1,
                batch_id=batch_id,
                facts=[fact],
                decisions=[],
                embed_client=_FakeEmbeddingClient(),
                ref_to_entity_id={},
                session_number=session_number,
            )

        facts = [item for item in db.added if isinstance(item, FactModel)]
        sources = [item for item in db.added if isinstance(item, FactSourceModel)]
        self.assertEqual(len(facts), 1)
        self.assertEqual(
            {(item.fact_id, item.session_id) for item in sources},
            {(101, 77), (101, 88)},
        )

    def test_same_text_with_different_source_time_reuses_fact(self) -> None:
        """忽略来源消息时间并累计事实来源。"""
        db = _FakeWriteSession(session_id=77)

        for batch_id, session_number, source_session_id, fact_ts in (
            ("batch-1", 3, 77, "2026-08-01"),
            ("batch-2", 4, 88, "2026-08-02"),
        ):
            db.session_id = source_session_id
            fact = {"text": "用户去了上海", "fact_ts": fact_ts}
            if batch_id != "batch-1":
                fact["_equivalent_fact_id"] = 101
            write_batch_results(
                db=db,
                task_id=1,
                batch_id=batch_id,
                facts=[fact],
                decisions=[],
                embed_client=_FakeEmbeddingClient(),
                ref_to_entity_id={},
                session_number=session_number,
            )

        facts = [item for item in db.added if isinstance(item, FactModel)]
        sources = [item for item in db.added if isinstance(item, FactSourceModel)]
        self.assertEqual(len(facts), 1)
        self.assertEqual(
            {(item.fact_id, item.session_id) for item in sources},
            {(101, 77), (101, 88)},
        )

    def test_semantic_match_reuses_fact_with_different_text(self) -> None:
        """根据等价事实 ID 复用不同表述并累计来源。"""
        db = _FakeWriteSession(session_id=77)
        write_batch_results(
            db=db,
            task_id=1,
            batch_id="batch-1",
            facts=[
                {
                    "text": "[PostgreSQL] 是用户偏好的数据库",
                    "fact_ts": "2026-08-01",
                }
            ],
            decisions=[],
            embed_client=_FakeEmbeddingClient(),
            ref_to_entity_id={},
            session_number=3,
        )

        db.session_id = 88
        write_batch_results(
            db=db,
            task_id=1,
            batch_id="batch-2",
            facts=[
                {
                    "text": "用户最喜欢的数据库是 [PostgreSQL]",
                    "fact_ts": "2026-08-02",
                    "_equivalent_fact_id": 101,
                }
            ],
            decisions=[],
            embed_client=_FakeEmbeddingClient(),
            ref_to_entity_id={},
            session_number=4,
        )

        facts = [item for item in db.added if isinstance(item, FactModel)]
        sources = [item for item in db.added if isinstance(item, FactSourceModel)]
        self.assertEqual(len(facts), 1)
        self.assertEqual(
            {(item.fact_id, item.session_id) for item in sources},
            {(101, 77), (101, 88)},
        )

    def test_semantic_match_adds_alias_to_reused_fact(self) -> None:
        """为语义复用的事实补写同一实体的新别名。"""
        db = _FakeWriteSession(session_id=77)
        write_batch_results(
            db=db,
            task_id=1,
            batch_id="batch-1",
            facts=[{"text": "[Robert] 喜欢茶", "fact_ts": "2026-08-01"}],
            decisions=[],
            embed_client=_FakeEmbeddingClient(),
            ref_to_entity_id={"Robert": "entity-person"},
            session_number=3,
        )

        db.session_id = 88
        write_batch_results(
            db=db,
            task_id=1,
            batch_id="batch-2",
            facts=[
                {
                    "text": "[Bob] 爱喝茶",
                    "fact_ts": "2026-08-02",
                    "_equivalent_fact_id": 101,
                }
            ],
            decisions=[],
            embed_client=_FakeEmbeddingClient(),
            ref_to_entity_id={"Bob": "entity-person"},
            session_number=4,
        )

        facts = [item for item in db.added if isinstance(item, FactModel)]
        refs = [item for item in db.added if isinstance(item, FactRefModel)]
        self.assertEqual(len(facts), 1)
        self.assertEqual(
            {(item.fact_id, item.entity_id, item.alias_text) for item in refs},
            {
                (101, "entity-person", "Robert"),
                (101, "entity-person", "Bob"),
            },
        )

    def test_different_semantic_time_text_remains_distinct(self) -> None:
        """保留语义时间标记不同的两条事实。"""
        db = _FakeWriteSession(session_id=77)

        for batch_id, session_number, fact_text in (
            ("batch-1", 3, "用户 [昨天::2026-08-01] 去了上海"),
            ("batch-2", 4, "用户 [昨天::2026-08-02] 去了上海"),
        ):
            write_batch_results(
                db=db,
                task_id=1,
                batch_id=batch_id,
                facts=[{"text": fact_text, "fact_ts": "2026-08-02"}],
                decisions=[],
                embed_client=_FakeEmbeddingClient(),
                ref_to_entity_id={},
                session_number=session_number,
            )

        facts = [item for item in db.added if isinstance(item, FactModel)]
        self.assertEqual(len(facts), 2)

    def test_exact_match_requires_same_stable_entity_id(self) -> None:
        """同名实体的稳定 ID 不同时拒绝精确文本复用。"""
        facts = [{"text": "[Alex] 喜欢茶"}]
        exact_rows = [(101, "[Alex] 喜欢茶")]
        fact_ref_rows = [(101, "entity-alex-a", "Alex")]

        same_entity = _match_exact_fact_ids(
            facts,
            [
                {
                    "batch_ref": "Alex",
                    "action": "merge",
                    "target_entity_id": "entity-alex-a",
                }
            ],
            exact_rows,
            fact_ref_rows,
        )
        different_entity = _match_exact_fact_ids(
            facts,
            [
                {
                    "batch_ref": "Alex",
                    "action": "merge",
                    "target_entity_id": "entity-alex-b",
                }
            ],
            exact_rows,
            fact_ref_rows,
        )

        self.assertEqual(same_entity, [101])
        self.assertEqual(different_entity, [None])

    def test_retrieved_facts_receive_all_source_chat_ids(self) -> None:
        """为每条事实补充全部外部聊天来源。"""
        facts = [
            RetrievedFact(fact_id=1, text="事实一", source="bm25"),
            RetrievedFact(fact_id=2, text="事实二", source="embed"),
        ]

        _inject_source_chat_ids(facts, _FakeReadSession())

        self.assertEqual(facts[0].source_chat_ids, ["chat-a", "chat-b"])
        self.assertEqual(facts[1].source_chat_ids, ["chat-c"])

    def test_retrieved_facts_resolve_same_alias_by_stable_entity(self) -> None:
        """按事实绑定解析同名实体，避免召回结果错误归因。"""
        facts = [
            RetrievedFact(fact_id=1, text="[Alex] 喜欢茶", source="bm25"),
            RetrievedFact(fact_id=2, text="[Alex] 喜欢咖啡", source="embed"),
        ]

        _resolve_pool_entity_refs(facts, _FakeEntityRefSession())

        self.assertEqual(facts[0].text, "Alex Chen 喜欢茶")
        self.assertEqual(facts[1].text, "Alex Wang 喜欢咖啡")

    def test_fact_score_applies_to_all_source_sessions(self) -> None:
        """为同一事实的全部来源会话生成聚合结果。"""
        facts = [
            RetrievedFact(
                fact_id=1,
                text="事实一",
                source="bm25",
                rerank_score=0.8,
            )
        ]

        sessions = aggregate_session_scores(
            facts,
            task_id=1,
            db=_FakeAggregateSession(),
            limit=10,
        )

        self.assertEqual({session.session_id for session in sessions}, {77, 88})
        self.assertTrue(all(session.score == 0.8 for session in sessions))


class DemoClientTest(unittest.IsolatedAsyncioTestCase):
    """验证 demo 客户端遵循必填聊天来源契约。"""

    async def test_push_conversation_sends_chat_id(self) -> None:
        """在记忆写入请求中携带外部聊天 ID。"""
        client = MembrainClient(base_url=None)
        fake_http = _FakeHttpClient()
        client._base_url = "http://membrain"
        client._http = fake_http

        success = await client.push_conversation(
            owner_id="user-1",
            persona_id="persona-1",
            chat_id="chat-1",
            messages=[
                {
                    "role": "user",
                    "content": "你好",
                    "created_at": "2026-08-02T10:00:00Z",
                }
            ],
            user_alias="用户",
            character_name="角色",
        )

        self.assertTrue(success)
        self.assertIsNotNone(fake_http.payload)
        self.assertEqual(fake_http.payload["chat_id"], "chat-1")


class FactEquivalenceWorkflowTest(unittest.IsolatedAsyncioTestCase):
    """验证事实等价解析只处理无法直接确定的事实。"""

    async def test_exact_match_skips_fact_resolver(self) -> None:
        """精确文本命中时直接复用事实且不创建 LLM agent。"""
        workflow = DefaultIngestWorkflow(
            _FakeEquivalenceStore([101], [[{"fact_id": 101}]]),
            None,
            None,
            None,
            _FakeAgentFactory(),
            None,
        )
        facts = [{"text": "用户喜欢茶"}]

        with patch.object(
            workflow._factory,
            "get_agent",
            side_effect=AssertionError("精确匹配不应调用 fact resolver"),
        ):
            await workflow._resolve_fact_equivalences(facts, [], 1)

        self.assertEqual(facts[0]["_equivalent_fact_id"], 101)

    async def test_fact_resolver_receives_stable_entity_ids(self) -> None:
        """用稳定实体 ID 告知 fact resolver 两个别名指向同一实体。"""
        registry = _CapturingRegistry()
        workflow = DefaultIngestWorkflow(
            _FakeEquivalenceStore(
                [None],
                [
                    [
                        {
                            "fact_id": 101,
                            "text": "[Robert] 喜欢茶",
                            "entities": [
                                {"ref": "Robert", "entity_id": "entity-person"}
                            ],
                        }
                    ]
                ],
            ),
            None,
            None,
            registry,
            _FakeAgentFactory(),
            None,
        )
        facts = [{"text": "[Bob] 爱喝茶"}]
        result = SimpleNamespace(
            output=SimpleNamespace(
                resolutions=[SimpleNamespace(new_fact_index=0, matched_fact_id=101)]
            )
        )

        with patch(
            "membrain.memory.application.ingest_workflow.run_agent_with_retry",
            new=AsyncMock(return_value=result),
        ):
            await workflow._resolve_fact_equivalences(
                facts,
                [
                    {
                        "batch_ref": "Bob",
                        "action": "merge",
                        "target_entity_id": "entity-person",
                    }
                ],
                1,
            )

        self.assertEqual(facts[0]["_equivalent_fact_id"], 101)
        self.assertEqual(
            registry.comparisons[0]["entities"],
            [{"ref": "Bob", "entity_id": "entity-person"}],
        )
        self.assertEqual(
            registry.comparisons[0]["candidates"][0]["entities"],
            [{"ref": "Robert", "entity_id": "entity-person"}],
        )

    async def test_fact_resolver_rejects_different_stable_entity_ids(self) -> None:
        """拒绝模型把主体稳定 ID 不同的候选事实判为等价。"""
        workflow = DefaultIngestWorkflow(
            _FakeEquivalenceStore(
                [None],
                [
                    [
                        {
                            "fact_id": 101,
                            "text": "[Alex] 喜欢茶",
                            "entities": [{"ref": "Alex", "entity_id": "entity-alex-a"}],
                        }
                    ]
                ],
            ),
            None,
            None,
            _CapturingRegistry(),
            _FakeAgentFactory(),
            None,
        )
        facts = [{"text": "[Alex] 喜欢茶"}]
        result = SimpleNamespace(
            output=SimpleNamespace(
                resolutions=[SimpleNamespace(new_fact_index=0, matched_fact_id=101)]
            )
        )

        with patch(
            "membrain.memory.application.ingest_workflow.run_agent_with_retry",
            new=AsyncMock(return_value=result),
        ):
            await workflow._resolve_fact_equivalences(
                facts,
                [
                    {
                        "batch_ref": "Alex",
                        "action": "merge",
                        "target_entity_id": "entity-alex-b",
                    }
                ],
                1,
            )

        self.assertNotIn("_equivalent_fact_id", facts[0])


class EntityResolverTest(unittest.IsolatedAsyncioTestCase):
    """验证同批实体不会被错误合并到同一稳定身份。"""

    async def test_same_batch_aliases_can_share_stable_target(self) -> None:
        """允许同批 Bob 与 Robert 共同合并到同一人物。"""
        target = SimpleNamespace(
            entity_id="entity-person",
            canonical_ref="Robert",
            desc="Robert is also called Bob",
        )
        candidate = SimpleNamespace(
            entity_id="entity-person",
            name="Robert",
            shingles={"unrelated"},
        )
        decisions = [
            {
                "batch_ref": "Bob",
                "action": "create",
                "target_ref": None,
                "canonical_ref": "Bob",
                "updated_desc": "Bob likes tea",
            },
            {
                "batch_ref": "Robert",
                "action": "create",
                "target_ref": None,
                "canonical_ref": "Robert",
                "updated_desc": "Robert likes tea",
            },
        ]
        llm_result = [
            ResolverDecision(
                new_entity_ref=ref,
                action="merge",
                target_entity_id="entity-person",
                resolved_via="llm",
            )
            for ref in ("Bob", "Robert")
        ]

        with (
            patch(
                "membrain.infra.retrieval.candidate_retrieval.retrieve_candidate_pool",
                return_value=(
                    [candidate],
                    {"entity-person": target},
                    {"entity-person": ["Bob"]},
                ),
            ),
            patch(
                "membrain.memory.core.entity_resolver.layer3_llm",
                new=AsyncMock(return_value=llm_result),
            ),
        ):
            resolved = await resolve_entities(
                decisions,
                db=object(),
                task_id=1,
                embed_client=None,
                registry=None,
                factory=None,
            )

        self.assertEqual(resolved[0]["action"], "merge")
        self.assertEqual(resolved[1]["action"], "merge")
        self.assertTrue(
            all(item["target_entity_id"] == "entity-person" for item in resolved)
        )

    async def test_same_surface_name_uses_description_context(self) -> None:
        """让 resolver 根据描述区分同名的 Alex Chen 与 Alex Wang。"""
        target = SimpleNamespace(
            entity_id="entity-alex-chen",
            canonical_ref="Alex",
            desc="Alex Chen works in Beijing",
        )
        candidate = SimpleNamespace(
            entity_id="entity-alex-chen",
            name="Alex",
            shingles={"ale", "lex"},
        )
        llm_result = [ResolverDecision(new_entity_ref="Alex", action="keep")]
        llm_mock = AsyncMock(return_value=llm_result)

        with (
            patch(
                "membrain.infra.retrieval.candidate_retrieval.retrieve_candidate_pool",
                return_value=(
                    [candidate],
                    {"entity-alex-chen": target},
                    {"entity-alex-chen": []},
                ),
            ),
            patch(
                "membrain.memory.core.entity_resolver.layer3_llm",
                new=llm_mock,
            ),
        ):
            resolved = await resolve_entities(
                [
                    {
                        "batch_ref": "Alex",
                        "action": "create",
                        "target_ref": None,
                        "canonical_ref": "Alex",
                        "updated_desc": "Alex Wang works in Shanghai",
                    }
                ],
                db=object(),
                task_id=1,
                embed_client=None,
                registry=None,
                factory=None,
            )

        llm_mock.assert_awaited_once()
        self.assertEqual(resolved[0]["action"], "create")

    async def test_llm_cannot_merge_person_or_different_owner_object(self) -> None:
        """拒绝 LLM 将人名或不同所有者的子实体合并到关联对象。"""
        for new_ref, target_ref, target_aliases, target_desc in (
            ("Blake", "AURORA-1001", [], "AURORA-1001 is an access code"),
            (
                "Blake",
                "access badge",
                ["Avery's access badge"],
                "The badge grants building access",
            ),
            (
                "Blake's access badge",
                "access badge",
                [],
                "Avery's access badge grants building access",
            ),
        ):
            with self.subTest(new_ref=new_ref, target_ref=target_ref):
                target = SimpleNamespace(
                    entity_id="entity-target",
                    canonical_ref=target_ref,
                    desc=target_desc,
                )
                candidate = SimpleNamespace(
                    entity_id="entity-target",
                    name=target_ref,
                    shingles={"unrelated"},
                )
                llm_result = [
                    ResolverDecision(
                        new_entity_ref=new_ref,
                        action="merge",
                        target_entity_id="entity-target",
                        resolved_via="llm",
                    )
                ]

                with (
                    patch(
                        "membrain.infra.retrieval.candidate_retrieval.retrieve_candidate_pool",
                        return_value=(
                            [candidate],
                            {"entity-target": target},
                            {"entity-target": target_aliases},
                        ),
                    ),
                    patch(
                        "membrain.memory.core.entity_resolver.layer3_llm",
                        new=AsyncMock(return_value=llm_result),
                    ),
                ):
                    resolved = await resolve_entities(
                        [
                            {
                                "batch_ref": new_ref,
                                "action": "create",
                                "target_ref": None,
                                "canonical_ref": new_ref,
                                "updated_desc": f"{new_ref} uses the target",
                            }
                        ],
                        db=object(),
                        task_id=1,
                        embed_client=None,
                        registry=None,
                        factory=None,
                    )

                self.assertEqual(resolved[0]["action"], "create")

    async def test_llm_can_merge_nonlexical_person_aliases(self) -> None:
        """允许 LLM 将 Bob 与 Robert 解析为同一人物。"""
        target = SimpleNamespace(
            entity_id="entity-person",
            canonical_ref="Robert",
            desc="Robert is also called Bob",
        )
        candidate = SimpleNamespace(
            entity_id="entity-person",
            name="Robert",
            shingles={"unrelated"},
        )
        llm_result = [
            ResolverDecision(
                new_entity_ref="Bob",
                action="merge",
                target_entity_id="entity-person",
                resolved_via="llm",
            )
        ]

        with (
            patch(
                "membrain.infra.retrieval.candidate_retrieval.retrieve_candidate_pool",
                return_value=(
                    [candidate],
                    {"entity-person": target},
                    {"entity-person": []},
                ),
            ),
            patch(
                "membrain.memory.core.entity_resolver.layer3_llm",
                new=AsyncMock(return_value=llm_result),
            ),
        ):
            resolved = await resolve_entities(
                [
                    {
                        "batch_ref": "Bob",
                        "action": "create",
                        "target_ref": None,
                        "canonical_ref": "Bob",
                        "updated_desc": "Bob likes tea",
                    }
                ],
                db=object(),
                task_id=1,
                embed_client=None,
                registry=None,
                factory=None,
            )

        self.assertEqual(resolved[0]["action"], "merge")
        self.assertEqual(resolved[0]["target_entity_id"], "entity-person")


if __name__ == "__main__":
    unittest.main()
