"""LoCoMo-specific dataset adapter."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from evaluation.ingest.adapters.base import (
    BaseAdapter,
    MessageSpec,
    QASpec,
    SessionSpec,
    TaskSpec,
    convert_evidence,
)

_logger = logging.getLogger(__name__)


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse LoCoMo timestamp like ``'1:56 pm on 8 May, 2023'``."""
    ts = ts.replace("\\s+", " ").strip()
    if not ts or ts.lower() == "unknown":
        return None
    try:
        return datetime.strptime(ts, "%I:%M %p on %d %B, %Y")
    except ValueError:
        _logger.warning("Failed to parse timestamp '%s'", ts)
        return None


def _evidence_ref(ref: str) -> str:
    """Convert LoCoMo evidence ref ``'D1:3'`` → ``'S1:2'`` (1-based → 0-based)."""
    m = re.match(r"^D(\d+):(\d+)$", ref)
    if m:
        return f"S{m.group(1)}:{int(m.group(2)) - 1}"
    return ref.replace("D", "S", 1)


_DATASET_DIR = Path(__file__).resolve().parents[2] / "dataset" / "locomo"

_CATEGORY_MAP: dict[int, str] = {
    1: "Multi Hop",
    2: "Temporal",
    3: "Open Domain",
    4: "Single Hop",
}


class LoCoMoAdapter(BaseAdapter):
    NAME = "locomo"

    def load_raw(self, dataset_name: str) -> list:
        files = sorted(_DATASET_DIR.glob("*.json"))
        if not files:
            raise FileNotFoundError(f"No JSON files found in {_DATASET_DIR}")
        if len(files) > 1:
            raise RuntimeError(
                f"Multiple JSON files in {_DATASET_DIR}: {[f.name for f in files]}. "
                "Override load_raw to specify which one to use."
            )
        with open(files[0], encoding="utf-8") as fh:
            return json.load(fh)

    def parse_item(self, item: dict, idx: int, dataset_name: str) -> TaskSpec:
        task_id = item.get("sample_id") or f"{dataset_name}_{idx}"
        conv = item.get("conversation", {})

        session_keys = sorted(
            [
                k
                for k in conv
                if k.startswith("session_") and not k.endswith("_date_time")
            ],
            key=lambda x: int(x.split("_")[1]),
        )

        sessions = []
        for skey in session_keys:
            num = int(skey.split("_")[1])
            raw_time = conv.get(f"{skey}_date_time")
            messages = []
            for msg in conv[skey]:
                text = msg["text"]
                blip = msg.get("blip_caption")
                if blip:
                    text = f"{text} [{blip}]" if text else f"[{blip}]"
                messages.append(
                    MessageSpec(
                        speaker=msg["speaker"],
                        content=text,
                    )
                )
            sessions.append(
                SessionSpec(
                    session_number=num,
                    session_time=_parse_timestamp(raw_time) if raw_time else None,
                    session_time_raw=raw_time,
                    messages=messages,
                )
            )

        qa_pairs = []
        for qa_idx, qa in enumerate(item.get("qa", [])):
            cat = qa.get("category")
            if cat not in _CATEGORY_MAP:
                continue
            qid = qa.get("question_id") or f"{dataset_name}_{idx}_qa{qa_idx}"
            cat_label = _CATEGORY_MAP[cat]
            qa_pairs.append(
                QASpec(
                    question_id=qid,
                    question=qa.get("question", ""),
                    answer=str(qa.get("answer", "")),
                    category=cat_label,
                    evidence=convert_evidence(
                        [_evidence_ref(e) for e in qa.get("evidence", [])]
                    ),
                )
            )

        return TaskSpec(
            task_id=task_id,
            sessions=sessions,
            qa_pairs=qa_pairs,
            agent_profile="locomo",
        )
