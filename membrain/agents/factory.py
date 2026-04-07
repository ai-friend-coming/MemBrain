"""Factory for creating PydanticAI agents from task manifests."""

from openai.types.chat import ChatCompletion
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from membrain.agents.registry import TaskRegistry


class _TolerantChatModel(OpenAIChatModel):
    """OpenAIChatModel that tolerates null usage fields from non-standard endpoints."""

    def _validate_completion(self, response: ChatCompletion) -> ChatCompletion:
        data = response.model_dump()
        usage = data.get("usage") or {}
        if usage.get("completion_tokens") is None:
            usage["completion_tokens"] = 0
        if usage.get("prompt_tokens") is None:
            usage["prompt_tokens"] = 0
        if usage.get("total_tokens") is None:
            usage["total_tokens"] = 0
        data["usage"] = usage
        return ChatCompletion.model_validate(data)


class AgentFactory:
    """Creates and caches PydanticAI agents from task definitions."""

    def __init__(self, task_registry: TaskRegistry, api_url: str, api_key: str):
        self.registry = task_registry
        self.api_url = api_url
        self.api_key = api_key
        self._cache: dict[tuple[str, str], tuple[Agent, ModelSettings | None]] = {}

    def get_agent(
        self, task_id: str, profile: str | None = None
    ) -> tuple[Agent, ModelSettings | None]:
        """Return the cached agent for a task, creating it on first call."""
        cache_key = (task_id, profile or "")
        if cache_key not in self._cache:
            self._cache[cache_key] = self._build(task_id, profile)
        return self._cache[cache_key]

    def _build(
        self, task_id: str, profile: str | None = None
    ) -> tuple[Agent, ModelSettings | None]:
        manifest = self.registry.get_manifest(task_id, profile)

        if manifest.type != "agent":
            raise ValueError(f"Task '{task_id}' is type '{manifest.type}', not 'agent'")

        model = _TolerantChatModel(
            manifest.model,
            provider=OpenAIProvider(base_url=self.api_url, api_key=self.api_key),
        )

        settings_kwargs: dict = {}
        if manifest.extra_body is not None:
            settings_kwargs["extra_body"] = manifest.extra_body
        if manifest.temperature is not None:
            settings_kwargs["temperature"] = manifest.temperature
        if manifest.max_tokens is not None:
            settings_kwargs["max_tokens"] = manifest.max_tokens
        if manifest.timeout is not None:
            settings_kwargs["timeout"] = manifest.timeout
        model_settings = ModelSettings(**settings_kwargs) if settings_kwargs else None

        output_model = self.registry.load_output_type(task_id, profile)
        output_type = output_model if output_model is not None else str
        agent = Agent(
            model=model,
            output_type=output_type,
            deps_type=dict,
            output_retries=3,
        )

        return agent, model_settings
