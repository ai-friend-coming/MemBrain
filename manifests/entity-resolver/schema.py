from pydantic import BaseModel, Field


class EntityResolution(BaseModel):
    new_entity_id: int = Field(description="ID from the new entities list (0-based)")
    matched_entity_id: int = Field(
        description="ID from the candidate list (0-based), or -1 if no match"
    )


class ReturnType(BaseModel):
    resolutions: list[EntityResolution]
