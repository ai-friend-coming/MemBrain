from typing import Literal

from pydantic import BaseModel


class ReturnType(BaseModel):
    result: Literal["fast_think", "deep_think"]
    vector_query: str | None = None
    keywords: list[str] | None = None
