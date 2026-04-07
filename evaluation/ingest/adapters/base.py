"""Base adapter interface and shared spec dataclasses for dataset ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


def convert_evidence(refs: list[str]) -> str:
    """Join a list of evidence ref strings into a comma-separated string."""
    return ", ".join(refs) if refs else ""


@dataclass
class MessageSpec:
    speaker: str
    content: str
    message_time: datetime | None = None
    message_time_raw: str | None = None


@dataclass
class SessionSpec:
    session_number: int
    messages: list[MessageSpec] = field(default_factory=list)
    session_time: datetime | None = None
    session_time_raw: str | None = None


@dataclass
class QASpec:
    question_id: str
    question: str
    answer: str
    evidence: str
    category: str | None = None
    options: str | None = None  # JSON string {"A":"...","B":"...",...} for MCQ
    reasoning: str | None = None


@dataclass
class TaskSpec:
    task_id: str
    sessions: list[SessionSpec] = field(default_factory=list)
    qa_pairs: list[QASpec] = field(default_factory=list)
    agent_profile: str | None = None


class BaseAdapter:
    # Subclasses set this to the dataset directory name under dataset/
    NAME: str = ""

    # Tail length kept for assistant messages; subclasses may override.
    _MAX_ASSISTANT_TAIL_CHARS: int = 400

    @classmethod
    def _truncate_assistant(cls, content: str) -> str:
        """Collapse excessive newlines, then tail-truncate with ellipsis prefix."""
        content = re.sub(r"\n{2,}", "\n", content)
        if len(content) > cls._MAX_ASSISTANT_TAIL_CHARS:
            content = "（……）" + content[-cls._MAX_ASSISTANT_TAIL_CHARS :]
        return content

    def load_raw(self, dataset_name: str) -> list:
        """Resolve and load source files for *dataset_name*.

        Returns the raw list of items that will be passed one-by-one to
        ``parse_item``.  Subclasses override this to handle their own file
        layout and format.
        """
        raise NotImplementedError

    def parse_item(self, item: dict, idx: int, dataset_name: str) -> TaskSpec:
        """Parse one raw item into a TaskSpec."""
        raise NotImplementedError

    def prepare_summary_messages(self, messages: list[dict]) -> list[dict]:
        """Return messages to pass to summarize_session_only. Default: raw messages."""
        return messages

    def prepare_ingest_messages(
        self, messages: list[dict], session_summary: str | None
    ) -> list[dict]:
        """Return messages to pass to ingest_batch. Default: raw messages."""
        return messages
