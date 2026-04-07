"""Dataset importer — loads raw benchmark data into the database."""

import logging

from sqlalchemy.orm import Session
from tqdm import tqdm

from evaluation.ingest.adapters.base import BaseAdapter, SessionSpec, TaskSpec
from evaluation.models.qa import QAPairModel
from membrain.infra.models.dataset import (
    ChatMessageModel,
    ChatSessionModel,
    DatasetModel,
    TaskModel,
)

_logger = logging.getLogger(__name__)


def _backfill_times(sessions: list[SessionSpec]) -> None:
    """Fill in missing session or message times where the other side has values."""
    for s in sessions:
        if not s.messages:
            continue
        msgs_all_empty = all(m.message_time is None for m in s.messages)
        msgs_all_have_time = all(m.message_time is not None for m in s.messages)

        if s.session_time is not None and msgs_all_empty:
            for m in s.messages:
                m.message_time = s.session_time
                m.message_time_raw = s.session_time_raw
        elif s.session_time is None and msgs_all_have_time:
            s.session_time = s.messages[0].message_time
            s.session_time_raw = s.messages[0].message_time_raw


def import_dataset(dataset_name: str, adapter: BaseAdapter, session: Session) -> int:
    """Import a dataset into the database using the given adapter.

    Calls ``adapter.load_raw(dataset_name)`` to resolve and load source files.
    Returns the new ``DatasetModel.id``.
    Raises ``ValueError`` if a dataset with *dataset_name* already exists.
    """
    existing = session.query(DatasetModel).filter_by(name=dataset_name).first()
    if existing:
        raise ValueError(f"Dataset '{dataset_name}' already exists (id={existing.id})")

    raw_data = adapter.load_raw(dataset_name)

    ds = DatasetModel(name=dataset_name)
    session.add(ds)
    session.flush()

    for idx, item in enumerate(tqdm(raw_data, desc=f"Importing {dataset_name}")):
        spec = adapter.parse_item(item, idx, dataset_name)
        _backfill_times(spec.sessions)
        _write_task(spec, ds, session)

    session.commit()
    return ds.id


def _write_task(spec: TaskSpec, ds: DatasetModel, session: Session) -> None:
    task = TaskModel(
        dataset_id=ds.id,
        task_id=spec.task_id,
        agent_profile=spec.agent_profile,
    )
    session.add(task)
    session.flush()

    for s in spec.sessions:
        cs = ChatSessionModel(
            task_id=task.id,
            session_number=s.session_number,
            session_time=s.session_time,
            session_time_raw=s.session_time_raw,
        )
        session.add(cs)
        session.flush()

        for pos, m in enumerate(s.messages):
            session.add(
                ChatMessageModel(
                    session_id=cs.id,
                    position=pos,
                    speaker=m.speaker,
                    content=m.content,
                    message_time=m.message_time,
                    message_time_raw=m.message_time_raw,
                )
            )

    for qa in spec.qa_pairs:
        session.add(
            QAPairModel(
                task_id=task.id,
                question_id=qa.question_id,
                question=qa.question,
                answer=qa.answer,
                category=qa.category,
                evidence=qa.evidence,
                options=qa.options,
                reasoning=qa.reasoning,
            )
        )
