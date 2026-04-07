"""LoCoMo dataset evaluation profile."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from evaluation.answering.profiles.base import BaseEvalProfile
from membrain.agents.factory import AgentFactory
from membrain.agents.registry import TaskRegistry
from membrain.agents.retry import run_agent_with_retry
from membrain.config import settings

if TYPE_CHECKING:
    from evaluation.models.qa import QAPairModel
    from evaluation.runtime.local_search import LocalSearchRunner

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MANIFESTS_DIR = str(_PROJECT_ROOT / "manifests")


class LoCoMoEvalProfile(BaseEvalProfile):
    def __init__(self, ranker: str = "rrf") -> None:
        super().__init__(ranker)
        self._registry = TaskRegistry(_MANIFESTS_DIR)
        self._factory = AgentFactory(
            self._registry, settings.LLM_API_URL, settings.LLM_API_KEY
        )

    def retrieve(
        self,
        client: "LocalSearchRunner",
        task_pk: int,
        qa: "QAPairModel",
        top_k: int,
        run_tag: str,
    ) -> str:
        result = client.search(
            task_pk=task_pk,
            question=qa.question,
            run_tag=run_tag,
            top_k=top_k,
        )
        return result["packed_context"]

    def generate_answer(
        self,
        qa: "QAPairModel",
        context_text: str,
        model: str,
        http_client: httpx.Client,
    ) -> str:
        agent, agent_settings = self._factory.get_agent("qa-answerer", profile="locomo")
        prompts = self._registry.render_prompts(
            "qa-answerer",
            profile="locomo",
            context=context_text,
            question=qa.question,
        )
        result = asyncio.run(
            run_agent_with_retry(
                agent, instructions=prompts, model_settings=agent_settings
            )
        )
        return result.output.final_answer
