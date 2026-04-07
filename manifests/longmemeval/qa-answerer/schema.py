from pydantic import BaseModel, Field


class ReturnType(BaseModel):
    cot: str = Field(
        description="Step-by-step chain-of-thought reasoning following the structured steps"
    )
    final_answer: str = Field(description="The direct, concise answer to the question")
