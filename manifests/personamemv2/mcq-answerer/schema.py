from typing import Literal

from pydantic import BaseModel, Field


class ReturnType(BaseModel):
    eliminated: list[Literal["A", "B", "C", "D"]] = Field(
        default_factory=list,
        description=(
            "Options ruled out by privacy violation, forgotten/Do-Not-Use content, "
            'or clear factual contradiction. List only letters, e.g. ["B", "C"]. '
            "Empty list if nothing is eliminated."
        ),
    )
    reasoning: str = Field(
        description=(
            "Step-by-step analysis: "
            "(1) privacy check — which options expose sensitive info, "
            "(2) forgotten/Do-Not-Use check — which options touch suppressed content, "
            "(3) elimination — list options removed and why, "
            "(4) contrastive comparison — for the two closest remaining options, "
            "identify the single most distinguishing fact, "
            "(5) final selection rationale"
        )
    )
    answer: Literal["A", "B", "C", "D"] = Field(
        description="The letter of the selected answer option"
    )
