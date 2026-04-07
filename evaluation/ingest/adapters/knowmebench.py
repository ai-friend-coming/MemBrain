"""Adapter for the KnowMeBench diary-based QA benchmark."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from evaluation.ingest.adapters.base import (
    BaseAdapter,
    MessageSpec,
    QASpec,
    SessionSpec,
    TaskSpec,
)

_DATASET_DIR = (
    Path(__file__).resolve().parents[2] / "dataset" / "knowmebench" / "KnowmeBench"
)
_SKIP_KEYS = {"id", "timestamp", "category"}


def _parse_ts(ts_str: str) -> datetime:
    """Parse timestamp supporting both space and T separators."""
    return datetime.strptime(ts_str.replace("T", " "), "%Y-%m-%d %H:%M:%S")


def _build_content(entry: dict) -> str:
    parts = []
    for k, v in entry.items():
        if k in _SKIP_KEYS or v is None:
            continue
        parts.append(f"[{k}: {v}]")
    return " ".join(parts) if parts else "(empty)"


def _parse_answer(ans_item: dict) -> tuple[str, str | None]:
    answer = ans_item.get("answer", "")
    if isinstance(answer, list):
        parts = []
        for item in answer:
            if isinstance(item, dict):
                rank = item.get("rank", "")
                event = item.get("event", str(item))
                parts.append(f"{rank}. {event}" if rank else str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts), None
    else:
        reasoning = ans_item.get("reasoning")
        valid = reasoning if isinstance(reasoning, str) and reasoning.strip() else None
        return str(answer), valid


def _get_evidence(ans_item: dict, entry_map: dict) -> str:
    ids = ans_item.get("evidence") or ans_item.get("evidence_ids") or []
    if isinstance(ids, int):
        ids = [ids]
    refs = []
    for eid in ids:
        pair = entry_map.get(eid)
        if pair and pair[0] is not None:
            refs.append(f"S{pair[0]}:{pair[1]}")
    return ", ".join(refs)


def _strip_category_suffix(stem: str) -> str:
    """Strip trailing _questions or _question from a file stem."""
    if stem.endswith("_questions"):
        return stem[: -len("_questions")]
    if stem.endswith("_question"):
        return stem[: -len("_question")]
    return stem


class KnowMeBenchAdapter(BaseAdapter):
    NAME = "knowmebench"

    def load_raw(self, dataset_name: str) -> list:
        return [{"dataset_num": 1}, {"dataset_num": 2}, {"dataset_num": 3}]

    def parse_item(self, item: dict, idx: int, dataset_name: str) -> TaskSpec:
        n = item["dataset_num"]
        sub_dir = _DATASET_DIR / f"dataset{n}"
        task_id = f"dataset{n}"

        # Load entries
        with open(sub_dir / "input" / f"dataset{n}.json") as f:
            entries: list[dict] = json.load(f)

        # Group by year; entries with empty timestamp inherit the previous entry's timestamp
        year_groups: dict[str, list[dict]] = {}
        last_ts = ""
        for e in entries:
            ts_raw = e.get("timestamp", "") or last_ts
            if not ts_raw:
                continue
            last_ts = ts_raw
            e = dict(e, timestamp=ts_raw)
            year = ts_raw[:4]
            year_groups.setdefault(year, []).append(e)

        sorted_years = sorted(year_groups.keys())

        sessions: list[SessionSpec] = []
        entry_map: dict[int, tuple[int, int]] = {}  # entry id → (session_num, pos)

        for snum, year in enumerate(sorted_years, start=1):
            group = year_groups[year]
            messages = []
            for pos, e in enumerate(group):
                entry_map[e["id"]] = (snum, pos)
                ts = _parse_ts(e["timestamp"])
                messages.append(
                    MessageSpec(
                        speaker="USER", content=_build_content(e), message_time=ts
                    )
                )
            session_time = _parse_ts(group[0]["timestamp"])
            sessions.append(
                SessionSpec(
                    session_number=snum, messages=messages, session_time=session_time
                )
            )

        # Load questions and answers
        question_dir = sub_dir / "question"
        answer_dir = sub_dir / "answer"

        qa_pairs: list[QASpec] = []

        for q_file in sorted(question_dir.iterdir()):
            if not q_file.suffix == ".json":
                continue
            category = _strip_category_suffix(q_file.stem)
            if category == "Logical Event Ordering":
                continue
            category_slug = category.lower().replace(" ", "_")

            # Find matching answer file
            a_file = None
            for suffix in ("_answers.json", "_answer.json"):
                candidate = answer_dir / (category + suffix)
                if candidate.exists():
                    a_file = candidate
                    break
            if a_file is None:
                continue

            with open(q_file) as f:
                questions = json.load(f)
            with open(a_file) as f:
                answers = json.load(f)

            answer_map = {str(a["id"]): a for a in answers}

            for q in questions:
                ans = answer_map.get(str(q["id"]))
                if ans is None:
                    continue
                answer_str, reasoning = _parse_answer(ans)
                evidence_str = _get_evidence(ans, entry_map)
                question_id = f"{category_slug}_{q['id']}"
                qa_pairs.append(
                    QASpec(
                        question_id=question_id,
                        question=q["question"],
                        answer=answer_str,
                        evidence=evidence_str,
                        category=category,
                        reasoning=reasoning,
                    )
                )

        return TaskSpec(
            task_id=task_id,
            sessions=sessions,
            qa_pairs=qa_pairs,
            agent_profile="knowmebench",
        )
