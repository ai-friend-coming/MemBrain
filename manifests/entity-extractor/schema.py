from pydantic import BaseModel, Field, model_validator


class ReturnType(BaseModel):
    entities: list[str] = Field(
        description="Deduplicated list of entity reference names extracted from the messages"
    )

    @model_validator(mode="after")
    def deduplicate_and_clean(self) -> "ReturnType":
        seen: set[str] = set()
        cleaned: list[str] = []
        for ref in self.entities:
            if ref and ref not in seen:
                seen.add(ref)
                cleaned.append(ref)
        self.entities = cleaned
        return self
