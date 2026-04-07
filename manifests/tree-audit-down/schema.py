from typing import Literal

from pydantic import BaseModel


class DownAction(BaseModel):
    action: Literal["GROUP"]
    target_ids: list[str]
    label: str


class ReturnType(BaseModel):
    actions: list[DownAction]
