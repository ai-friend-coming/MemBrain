"""LongMemEval-specific dataset adapter.

Evidence strategy:
  - Primary: oracle JSON (longmemeval_oracle.json) — message-level evidence via
    ``has_answer: true`` on individual messages → ``S{n}:{pos}`` refs
  - Fallback: session-level evidence from ``answer_session_ids`` → ``S{n}`` refs
    (used when oracle is absent or a task has no has_answer annotations)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from evaluation.ingest.adapters.base import (
    BaseAdapter,
    MessageSpec,
    QASpec,
    SessionSpec,
    TaskSpec,
)

_logger = logging.getLogger(__name__)

_DATASET_DIR = Path(__file__).resolve().parents[2] / "dataset" / "longmemeval"
_ORACLE_PATH = _DATASET_DIR / "longmemeval_oracle.json"
_CLEANED_PATH = _DATASET_DIR / "longmemeval_s_cleaned.json"


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse LongMemEval timestamp like ``'2023/05/20 (Sat) 02:21'``."""
    ts = ts.strip()
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y/%m/%d (%a) %H:%M")
    except ValueError:
        _logger.warning("Failed to parse timestamp '%s'", ts)
        return None


def _message_level_evidence(
    haystack_sessions: list[list[dict]],
) -> list[str]:
    """Extract S{n}:{pos} refs (0-based) from messages with has_answer=true."""
    refs: list[str] = []
    for i, sess in enumerate(haystack_sessions):
        sess_num = i + 1
        for pos, msg in enumerate(sess):
            if msg.get("has_answer"):
                refs.append(f"S{sess_num}:{pos}")
    return refs


def _session_level_evidence(
    answer_session_ids: list[str],
    haystack_session_ids: list[str],
) -> list[str]:
    """Fall back to S{n} refs from answer_session_ids."""
    id_to_num = {sid: i + 1 for i, sid in enumerate(haystack_session_ids)}
    return [f"S{id_to_num[aid]}" for aid in answer_session_ids if aid in id_to_num]


class LongMemEvalAdapter(BaseAdapter):
    NAME = "longmemeval"

    _MAX_USER_MSG_CHARS = 2000

    def _process_messages(self, messages: list[dict]) -> list[dict]:
        result = []
        for m in messages:
            if m["speaker"] == "assistant":
                result.append({**m, "content": self._truncate_assistant(m["content"])})
            else:
                content = m["content"]
                if len(content) > self._MAX_USER_MSG_CHARS:
                    content = content[: self._MAX_USER_MSG_CHARS]
                result.append({**m, "content": content})
        return result

    def prepare_summary_messages(self, messages: list[dict]) -> list[dict]:
        return self._process_messages(messages)

    def prepare_ingest_messages(
        self, messages: list[dict], session_summary: str | None
    ) -> list[dict]:
        """Return messages with per-role truncation.

        User messages: truncated to _MAX_USER_MSG_CHARS from the front.
        Assistant messages: excessive newlines collapsed, then tail-truncated
        to _MAX_ASSISTANT_TAIL_CHARS; if truncated, prefixed with （……）.
        """
        return self._process_messages(messages)

    def load_raw(self, dataset_name: str) -> list:
        if not _CLEANED_PATH.exists():
            raise FileNotFoundError(f"No LongMemEval data file found in {_DATASET_DIR}")

        _logger.info("Loading LongMemEval sessions from cleaned: %s", _CLEANED_PATH)
        with open(_CLEANED_PATH, encoding="utf-8") as fh:
            data = json.load(fh)

        if _ORACLE_PATH.exists():
            _logger.info("Overlaying has_answer markers from oracle: %s", _ORACLE_PATH)
            with open(_ORACLE_PATH, encoding="utf-8") as fh:
                oracle_list = json.load(fh)

            # Build lookup: question_id → session_id → set of has_answer message positions
            oracle_map: dict[str, dict[str, set[int]]] = {}
            for item in oracle_list:
                qid = item["question_id"]
                sess_evidence: dict[str, set[int]] = {}
                for sess_id, sess_msgs in zip(
                    item["haystack_session_ids"], item["haystack_sessions"]
                ):
                    for pos, msg in enumerate(sess_msgs):
                        if msg.get("has_answer"):
                            sess_evidence.setdefault(sess_id, set()).add(pos)
                oracle_map[qid] = sess_evidence

            # Annotate cleaned sessions in-place
            for item in data:
                qid = item.get("question_id", "")
                if qid not in oracle_map:
                    continue
                sess_evidence = oracle_map[qid]
                for sess_id, sess_msgs in zip(
                    item["haystack_session_ids"], item["haystack_sessions"]
                ):
                    if sess_id not in sess_evidence:
                        continue
                    for pos, msg in enumerate(sess_msgs):
                        if pos in sess_evidence[sess_id]:
                            msg["has_answer"] = True

        return data

    def parse_item(self, item: dict, idx: int, dataset_name: str) -> TaskSpec:
        task_id = item.get("question_id") or f"{dataset_name}_{idx}"

        haystack_dates = item.get("haystack_dates", [])
        haystack_sessions = item.get("haystack_sessions", [])
        haystack_session_ids = item.get("haystack_session_ids", [])

        sessions: list[SessionSpec] = []
        for i, (turns, raw_date) in enumerate(
            zip(haystack_sessions, haystack_dates), start=1
        ):
            messages = [
                MessageSpec(speaker=t["role"], content=t["content"]) for t in turns
            ]
            sessions.append(
                SessionSpec(
                    session_number=i,
                    session_time=_parse_timestamp(raw_date),
                    session_time_raw=raw_date,
                    messages=messages,
                )
            )

        # Evidence: message-level when available, session-level as fallback
        evidence_refs = _message_level_evidence(haystack_sessions)
        if not evidence_refs:
            evidence_refs = _session_level_evidence(
                item.get("answer_session_ids", []), haystack_session_ids
            )

        qa = QASpec(
            question_id=task_id,
            question=item.get("question", ""),
            answer=str(item.get("answer", "")),
            category=item.get("question_type"),
            evidence=", ".join(evidence_refs),
        )

        return TaskSpec(
            task_id=task_id,
            sessions=sessions,
            qa_pairs=[qa],
            agent_profile="longmemeval",
        )
