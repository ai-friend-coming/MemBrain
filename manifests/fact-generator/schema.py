import re

from pydantic import BaseModel, Field, field_validator, model_validator

# ISO 8601 date/datetime/year/month pattern (point or interval)
_ISO_POINT = r"(\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2})?Z?)?)?)?)"
_ISO_INTERVAL = rf"({_ISO_POINT})?/({_ISO_POINT})?"

_ISO_RE = re.compile(rf"^({_ISO_INTERVAL}|{_ISO_POINT[1:-1]})$")

# [raw::resolved] time token
_TIME_TOKEN_RE = re.compile(r"\[([^\[\]]+?)::([^\[\]]+)\]")
# [entity] bracket — no ::
_ENTITY_TOKEN_RE = re.compile(r"\[([^\[\]:]+)\]")


def _is_valid_iso(s: str) -> bool:
    return bool(_ISO_RE.match(s))


class FactEntry(BaseModel):
    text: str = Field(
        description=(
            "Atomic self-contained fact with entities in [square brackets] and "
            "resolved time expressions as [raw::resolved] tokens. "
            "Unresolvable time expressions stay as plain text without brackets."
        )
    )
    fact_ts: str | None = Field(
        description=(
            "Timestamp of the source message this fact came from, copied verbatim "
            "from the message header (e.g. '2024-03-15T14:03:00Z'). "
            "If the fact spans multiple messages, use the last one. Null if truly indeterminate."
        )
    )

    @field_validator("fact_ts")
    @classmethod
    def validate_fact_ts(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _is_valid_iso(v):
            raise ValueError(f"fact_ts is not valid ISO 8601: {v!r}")
        return v


class ReturnType(BaseModel):
    facts: list[FactEntry] = Field(
        description="Atomic facts with inline temporal annotations"
    )

    @model_validator(mode="after")
    def check_integrity(self) -> "ReturnType":
        errors: list[str] = []

        for i, fact in enumerate(self.facts):
            text = fact.text

            # Validate resolved parts of time tokens
            for raw, resolved in _TIME_TOKEN_RE.findall(text):
                if not _is_valid_iso(resolved):
                    errors.append(
                        f"facts[{i}] time token [{raw}::{resolved}] has invalid ISO 8601 "
                        f"resolved value — use a valid ISO 8601 date/datetime "
                        f"(e.g. 2024-03, 2024-03-15, 2024-03-15T14:00:00Z). "
                        f"If the time boundary is indeterminate, leave it as plain text without brackets."
                    )

            # Every fact must have at least one [entity] bracket
            entity_mentions = set(_ENTITY_TOKEN_RE.findall(text))
            if not entity_mentions:
                errors.append(
                    f"facts[{i}] has no [entity] brackets — every fact must wrap at least one "
                    f"entity name in [square brackets] matching the entity list. "
                    f"Problematic fact: {text!r}"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self
