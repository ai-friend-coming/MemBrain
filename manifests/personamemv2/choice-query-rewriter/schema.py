from pydantic import BaseModel, Field


class ReturnType(BaseModel):
    Q: str = Field(
        description=(
            "BM25 keyword query for the question itself. "
            "Strip question structure (e.g. 'What are some', 'How can I', 'I'm looking for') "
            "and keep only the core topic keywords that identify what kind of memory to search."
        )
    )
    A: str = Field(
        description=(
            "BM25 keyword query for the user preference uniquely assumed by option A. "
            "Focus on what distinguishes A from the other three options, not on the shared topic."
        )
    )
    B: str = Field(
        description=(
            "BM25 keyword query for the user preference uniquely assumed by option B. "
            "Focus on what distinguishes B from the other three options, not on the shared topic."
        )
    )
    C: str = Field(
        description=(
            "BM25 keyword query for the user preference uniquely assumed by option C. "
            "Focus on what distinguishes C from the other three options, not on the shared topic."
        )
    )
    D: str = Field(
        description=(
            "BM25 keyword query for the user preference uniquely assumed by option D. "
            "Focus on what distinguishes D from the other three options, not on the shared topic."
        )
    )
