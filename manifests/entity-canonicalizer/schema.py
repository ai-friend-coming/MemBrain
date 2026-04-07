from pydantic import BaseModel, Field, model_validator


class CanonicalizedEntity(BaseModel):
    idx: int = Field(description="0-based index matching the input list")
    canonical_ref: str = Field(
        description="Best canonical name, must be one of the aliases"
    )
    merged_desc: str = Field(
        description="Merged description, ≤50 tokens, static facts only"
    )


class ReturnType(BaseModel):
    results: list[CanonicalizedEntity]

    @model_validator(mode="after")
    def check_structural(self) -> "ReturnType":
        errors: list[str] = []
        seen: set[int] = set()
        for r in self.results:
            if r.idx in seen:
                errors.append(f"Duplicate idx {r.idx}")
            seen.add(r.idx)
            if len(r.merged_desc.split()) > 60:
                errors.append(
                    f"idx={r.idx} desc too long: {len(r.merged_desc.split())} words"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self
