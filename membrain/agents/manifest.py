"""Task manifest configuration models."""

from typing import Literal

from pydantic import BaseModel, Field


class ParameterDef(BaseModel):
    """Definition for a single prompt parameter."""

    required: bool = False
    default: str = ""


class PromptConfig(BaseModel):
    """Configuration for a single system prompt template."""

    template: str


class ManifestConfig(BaseModel):
    """Complete task manifest configuration."""

    task_id: str
    type: Literal["agent", "template"]
    description: str = ""
    model: str
    extra_body: dict | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    parameters: dict[str, ParameterDef] = Field(default_factory=dict)
    prompts: list[PromptConfig] = Field(default_factory=list)
    timeout: int | None = None
    output_schema: str | None = Field(default=None, validation_alias="schema")
