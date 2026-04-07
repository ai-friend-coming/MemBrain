"""Shared service managers for the HTTP API layer.

Exports:
  TaskServiceManager  — lazy-init session workflow + resources per task_pk
  task_mgr            — module-level TaskServiceManager instance
  SearchServiceManager — shared engine + clients for read-only memory search
  search_mgr          — module-level SearchServiceManager instance
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.config import settings
from membrain.infra.clients.embedding import EmbeddingClient
from membrain.infra.db import create_run_engine, init_run_schema
from membrain.memory.application.session_memory_workflow import (
    SessionMemoryWorkflow,
    build_session_memory_workflow,
)

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = str(_PROJECT_ROOT / "manifests")

_RUN_TAG = "default"


class TaskServiceManager:
    """Lazy-init and cache session workflows per task_pk."""

    def __init__(self) -> None:
        self._workflows: dict[int, SessionMemoryWorkflow] = {}
        self._engines: dict[int, Engine] = {}
        self._session_factories: dict[int, sessionmaker] = {}
        self._embed_clients: dict[int, EmbeddingClient] = {}
        self._registries: dict[int, TaskRegistry] = {}
        self._factories: dict[int, AgentFactory] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, task_pk: int) -> asyncio.Lock:
        if task_pk not in self._locks:
            self._locks[task_pk] = asyncio.Lock()
        return self._locks[task_pk]

    def get_or_create(self, task_pk: int) -> SessionMemoryWorkflow:
        if task_pk not in self._workflows:
            eng = create_run_engine(task_pk, _RUN_TAG)
            init_run_schema(eng, task_pk, _RUN_TAG)
            sf = sessionmaker(bind=eng)
            ec = EmbeddingClient()
            registry = TaskRegistry(MANIFESTS_DIR)
            af = AgentFactory(registry, settings.LLM_API_URL, settings.LLM_API_KEY)
            workflow = build_session_memory_workflow(
                session_factory=sf,
                embed_client=ec,
                registry=registry,
                factory=af,
            )
            self._engines[task_pk] = eng
            self._session_factories[task_pk] = sf
            self._embed_clients[task_pk] = ec
            self._registries[task_pk] = registry
            self._factories[task_pk] = af
            self._workflows[task_pk] = workflow
        return self._workflows[task_pk]

    def cleanup(self, task_pk: int) -> None:
        if task_pk in self._embed_clients:
            self._embed_clients.pop(task_pk).close()
        if task_pk in self._engines:
            self._engines.pop(task_pk).dispose()
        self._workflows.pop(task_pk, None)
        self._session_factories.pop(task_pk, None)
        self._registries.pop(task_pk, None)
        self._factories.pop(task_pk, None)

    def cleanup_all(self) -> None:
        for key in list(self._workflows):
            self.cleanup(key)


task_mgr = TaskServiceManager()


class SearchServiceManager:
    """Shared engine + global clients for read-only memory search."""

    def __init__(self) -> None:
        self._engine: Engine | None = None
        self._sf: sessionmaker | None = None
        self._embed_client: EmbeddingClient | None = None
        self._http_client: httpx.Client | None = None

    def _ensure_engine(self) -> None:
        if self._engine is None:
            self._engine = sa_create_engine(
                settings.DATABASE_URL,
                pool_pre_ping=True,
                pool_size=settings.QA_SEARCH_POOL_SIZE,
                max_overflow=10,
                pool_timeout=settings.DB_POOL_TIMEOUT,
                pool_recycle=settings.DB_POOL_RECYCLE,
            )
            self._sf = sessionmaker(bind=self._engine)

    def get_session_factory(self) -> sessionmaker:
        self._ensure_engine()
        return self._sf  # type: ignore[return-value]

    def get_embed_client(self) -> EmbeddingClient:
        if self._embed_client is None:
            self._embed_client = EmbeddingClient()
        return self._embed_client

    def get_http_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=60.0)
        return self._http_client

    def cleanup_all(self) -> None:
        if self._embed_client:
            self._embed_client.close()
            self._embed_client = None
        if self._http_client:
            self._http_client.close()
            self._http_client = None
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._sf = None


search_mgr = SearchServiceManager()
