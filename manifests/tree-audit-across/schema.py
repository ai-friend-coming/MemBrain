from typing import Literal

from pydantic import BaseModel


class AcrossAction(BaseModel):
    action: Literal["PROMOTE", "RELOCATE"]
    target_id: str
    destination_id: str | None = None


class ReturnType(BaseModel):
    actions: list[AcrossAction]
