import re

from pydantic import BaseModel, Field, field_validator, model_validator

# [raw::resolved] time token
_TIME_TOKEN_RE = re.compile(r"\[([^\[\]]+?)::([^\[\]]+)\]")
# [entity] bracket — no ::
_ENTITY_TOKEN_RE = re.compile(r"\[([^\[\]:]+)\]")


class FactEntry(BaseModel):
    text: str = Field(
        description=(
            "Atomic self-contained fact with entities in [square brackets]. "
            "Every fact must reference [User]."
        )
    )
    fact_ts: str | None = None

    @field_validator("fact_ts", mode="before")
    @classmethod
    def force_null(cls, v) -> None:
        return None


class ReturnType(BaseModel):
    facts: list[FactEntry] = Field(
        description="Atomic facts extracted from the session summary"
    )

    @model_validator(mode="after")
    def check_integrity(self) -> "ReturnType":
        errors: list[str] = []

        for i, fact in enumerate(self.facts):
            entity_mentions = set(_ENTITY_TOKEN_RE.findall(fact.text))
            if not entity_mentions:
                errors.append(
                    f"facts[{i}] has no [entity] brackets — every fact must wrap at least one "
                    f"entity name in [square brackets]. Problematic fact: {fact.text!r}"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self
