"""PersonaMem v2 dataset adapter.

Reads benchmark.csv and per-persona chat history JSON files directly into the
MemBrain database.  Questions are stored as multiple-choice QA pairs.
"""

import ast
import csv
import json
import logging
import random
from pathlib import Path

from evaluation.ingest.adapters.base import (
    BaseAdapter,
    MessageSpec,
    QASpec,
    SessionSpec,
    TaskSpec,
)
from membrain.config import settings

_logger = logging.getLogger(__name__)

_DATASET_DIR = (
    Path(__file__).resolve().parents[2] / "dataset" / "personamemv2" / "PersonaMem-v2"
)

_LABELS = ["A", "B", "C", "D"]


def _split_sessions(
    messages: list,
    size: int,
    overlap: int = 0,
) -> tuple[list[SessionSpec], list[int]]:
    """Split a flat message list into SessionSpecs, returning (sessions, session_starts).

    When ``total % size == 0`` (the list divides evenly), non-overlapping
    windows of exactly *size* are produced with step = *size*.

    When the list does not divide evenly, a sliding-window approach is used
    with step = ``size - overlap`` so that consecutive windows share *overlap*
    messages, providing context continuity at session boundaries.  The last
    window is clamped to the end of the list.
    """
    if not messages:
        return [], []

    total = len(messages)
    sessions: list[SessionSpec] = []
    session_starts: list[int] = []
    session_num = 1

    if overlap > 0 and total % size != 0:
        step = size - overlap
    else:
        step = size

    start = 0
    while start < total:
        end = min(start + size, total)
        sessions.append(
            SessionSpec(session_number=session_num, messages=messages[start:end])
        )
        session_starts.append(start)
        session_num += 1
        if end >= total:
            break
        start += step

    return sessions, session_starts


def _load_persona_name(raw_persona_file: str) -> str:
    """Load persona name from the raw persona JSON file.

    Tries several structural variants found in the dataset:
      - {id: {"name": ...}}                           (common)
      - {id: {"full_persona": {"name": ...}}}         (e.g. persona281)
      - {id: {"detailed_persona": {"name": ...}}}
      - {id: {"individual_details": {"name": ...}}}
      - {id: {"demographics": {"members": [{"name": ...}]}}}  (group, e.g. persona974)
    """
    path = _DATASET_DIR / raw_persona_file
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        inner = next(iter(data.values()))
        if "name" in inner:
            return inner["name"]
        for key in ("full_persona", "detailed_persona", "individual_details"):
            if isinstance(inner.get(key), dict) and "name" in inner[key]:
                return inner[key]["name"]
        members = inner.get("demographics", {}).get("members")
        if members and isinstance(members, list) and "name" in members[0]:
            return members[0]["name"]
    except Exception as exc:
        _logger.warning("Could not load persona name from %s: %s", path, exc)
    return "User"


def _build_options(correct: str, incorrect_raw: str, seed: str) -> tuple[dict, str]:
    """Shuffle correct + incorrect answers into labeled options.

    Returns (options_dict, correct_label).
    Uses *seed* for deterministic, non-biased assignment.
    """
    try:
        incorrect = ast.literal_eval(incorrect_raw)
    except Exception:
        _logger.warning("Failed to parse incorrect_answers: %s", incorrect_raw[:60])
        incorrect = []

    choices = [correct] + list(incorrect)
    rng = random.Random(seed)
    rng.shuffle(choices)

    options = dict(zip(_LABELS[: len(choices)], choices))
    correct_label = next((k for k, v in options.items() if v == correct), _LABELS[0])
    return options, correct_label


class PersonaMemV2Adapter(BaseAdapter):
    NAME = "personamemv2"
    VIRTUAL_SESSION_SIZE: int = settings.PERSONAMEM_VIRTUAL_SESSION_SIZE
    VIRTUAL_SESSION_OVERLAP: int = 0

    def load_raw(self, dataset_name: str) -> list:
        benchmark = _DATASET_DIR / "benchmark" / "text" / "benchmark.csv"
        if not benchmark.exists():
            raise FileNotFoundError(f"Expected benchmark file not found: {benchmark}")

        grouped: dict[str, dict] = {}
        with open(benchmark, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                pid = row["persona_id"]
                if pid not in grouped:
                    grouped[pid] = {
                        "persona_id": pid,
                        "chat_history_link": row["chat_history_32k_link"],
                        "questions": [],
                    }
                grouped[pid]["questions"].append(row)

        return list(grouped.values())

    def parse_item(self, item: dict, idx: int, dataset_name: str) -> TaskSpec:
        persona_id = item["persona_id"]
        task_id = f"persona{persona_id}"

        # Load chat history
        history_path = _DATASET_DIR / item["chat_history_link"]
        try:
            with open(history_path, encoding="utf-8") as fh:
                history_data = json.load(fh)
            chat_history = history_data.get("chat_history", [])
        except Exception as exc:
            _logger.warning("Could not load chat history for %s: %s", task_id, exc)
            chat_history = []

        # Load persona name from raw persona JSON (via CSV column)
        persona_name = _load_persona_name(item["questions"][0]["raw_persona_file"])

        # Build session (skip system messages), tracking position for evidence lookup
        messages: list[MessageSpec] = []
        # Map content prefix (first 60 chars) → list of positions for evidence matching
        _content_to_pos: dict[str, list[int]] = {}
        for msg in chat_history:
            role = msg.get("role", "")
            if role == "system":
                continue
            pos = len(messages)
            content = msg.get("content", "")
            prefix = content[:60]
            _content_to_pos.setdefault(prefix, []).append(pos)
            speaker = persona_name if role == "user" else "assistant"
            messages.append(MessageSpec(speaker=speaker, content=content))

        sessions, session_starts = _split_sessions(
            messages,
            size=self.VIRTUAL_SESSION_SIZE,
        )

        # Build flat-position → session-relative evidence ref.
        flat_to_ref: dict[int, str] = {}
        for sess, flat_start in zip(sessions, session_starts):
            for within_pos in range(len(sess.messages)):
                flat_to_ref[flat_start + within_pos] = (
                    f"S{sess.session_number}:{within_pos}"
                )

        # Build QA pairs
        qa_pairs: list[QASpec] = []
        for q_idx, row in enumerate(item["questions"]):
            try:
                question = ast.literal_eval(row["user_query"])["content"]
            except Exception:
                question = row.get("user_query", "")

            options_dict, correct_label = _build_options(
                correct=row["correct_answer"],
                incorrect_raw=row["incorrect_answers"],
                seed=question,
            )

            # Build evidence refs from related_conversation_snippet
            evidence = ""
            try:
                snippet = ast.literal_eval(row["related_conversation_snippet"])
                positions: list[int] = []
                used: set[int] = set()
                for sm in snippet:
                    prefix = sm.get("content", "")[:60]
                    for p in _content_to_pos.get(prefix, []):
                        if p not in used:
                            positions.append(p)
                            used.add(p)
                            break
                if positions:
                    refs = [
                        flat_to_ref[p] for p in sorted(positions) if p in flat_to_ref
                    ]
                    evidence = ", ".join(refs)
            except Exception:
                pass

            qa_pairs.append(
                QASpec(
                    question_id=f"{task_id}_q{q_idx}",
                    question=question,
                    answer=correct_label,
                    evidence=evidence,
                    category=row.get("conversation_scenario"),
                    options=json.dumps(options_dict, ensure_ascii=False),
                )
            )

        return TaskSpec(
            task_id=task_id,
            sessions=sessions,
            qa_pairs=qa_pairs,
            agent_profile="personamemv2",
        )

    def _process_messages(self, messages: list[dict]) -> list[dict]:
        result = []
        for m in messages:
            if m["speaker"] == "assistant":
                result.append({**m, "content": self._truncate_assistant(m["content"])})
            else:
                result.append(m)
        return result

    def prepare_summary_messages(self, messages: list[dict]) -> list[dict]:
        return self._process_messages(messages)

    def prepare_ingest_messages(
        self, messages: list[dict], session_summary: str | None
    ) -> list[dict]:
        if session_summary:
            return [{"speaker": "", "content": session_summary, "message_time": ""}]
        return self._process_messages(messages)
