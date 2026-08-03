from pydantic import BaseModel, Field


class FactResolution(BaseModel):
    """返回一条新事实的等价候选匹配。"""

    new_fact_index: int = Field(description="Index of the new fact")
    matched_fact_id: int | None = Field(
        description="ID of the equivalent stored fact, or null when none is equivalent"
    )


class ReturnType(BaseModel):
    """承载当前批次全部新事实的等价解析结果。"""

    resolutions: list[FactResolution]
