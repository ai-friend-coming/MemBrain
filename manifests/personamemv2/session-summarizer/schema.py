from pydantic import BaseModel, Field


class ReturnType(BaseModel):
    preferences: str = Field(
        description=(
            "Categorized preference profile of the user. "
            "Plain text, grouped by category, one entry per line. "
            "Must NOT include any item the user has asked to forget."
        )
    )
    forgotten_by_user: list[str] = Field(
        default_factory=list,
        description=(
            "Items the user explicitly asked to be forgotten. "
            "Each entry is a short description of what was requested to be forgotten. "
            "Empty list if no forget requests were made. "
            "Do NOT annotate entries with phrases like 'user requested forgetting this'."
        ),
    )
