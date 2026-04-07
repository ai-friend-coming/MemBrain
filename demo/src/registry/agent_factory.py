"""Factory for creating agents from task manifests."""

from openai.types.chat import ChatCompletion as _ChatCompletion
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from .task_registry import TaskRegistry


class _CompatibleOpenAIChatModel(OpenAIChatModel):
    """OpenAIChatModel subclass that tolerates None usage fields from non-official APIs."""

    def _validate_completion(self, response: _ChatCompletion) -> _ChatCompletion:
        data = response.model_dump()
        usage = data.get("usage") or {}
        if usage:
            usage.setdefault("completion_tokens", 0)
            if usage["completion_tokens"] is None:
                usage["completion_tokens"] = 0
            usage.setdefault("prompt_tokens", 0)
            if usage["prompt_tokens"] is None:
                usage["prompt_tokens"] = 0
            usage.setdefault("total_tokens", 0)
            if usage["total_tokens"] is None:
                usage["total_tokens"] = 0
            data["usage"] = usage
        return _ChatCompletion.model_validate(data)


def extract_first_json(text: str) -> str:
    """Strip whitespace/markdown fences and truncate to the first balanced JSON object.

    Used as a best-effort sanitizer before re-validating a response that failed
    pydantic-ai's normal output validation (e.g. trailing '}' from glm-4.6).
    """
    text = text.strip()
    if text.startswith("```"):
        import re

        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return text[: i + 1]
    return text


class AgentFactory:
    """Creates and caches PydanticAI agents from task definitions."""

    def __init__(self, task_registry: TaskRegistry, api_url: str, api_key: str):
        self.registry = task_registry
        self.api_url = api_url
        self.api_key = api_key
        self._cache: dict[str, tuple[Agent, ModelSettings | None]] = {}

    def get_agent(self, task_id: str) -> tuple[Agent, ModelSettings | None]:
        """
        Return the cached agent for a task, creating it on first call.

        The agent uses deps (dict) for render params so the system prompt is
        rendered dynamically per request without re-creating the agent.
        """
        if task_id not in self._cache:
            self._cache[task_id] = self._build(task_id)
        return self._cache[task_id]

    def _build(self, task_id: str) -> tuple[Agent, ModelSettings | None]:
        manifest = self.registry.get_manifest(task_id)

        if manifest.type != "agent":
            raise ValueError(f"Task '{task_id}' is type '{manifest.type}', not 'agent'")

        model = _CompatibleOpenAIChatModel(
            manifest.model,
            provider=OpenAIProvider(base_url=self.api_url, api_key=self.api_key),
        )
        settings_kwargs = {}
        if manifest.extra_body is not None:
            settings_kwargs["extra_body"] = manifest.extra_body
        if manifest.temperature is not None:
            settings_kwargs["temperature"] = manifest.temperature
        if manifest.max_tokens is not None:
            settings_kwargs["max_tokens"] = manifest.max_tokens
        model_settings = ModelSettings(**settings_kwargs) if settings_kwargs else None

        output_model = self.registry.load_output_type(task_id)
        output_type = output_model if output_model is not None else str
        agent = Agent(model=model, output_type=output_type, deps_type=dict, output_retries=2)

        return agent, model_settings
