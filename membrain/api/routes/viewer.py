"""Read-only viewer API — dataset and memory graph inspection.

Endpoints:
  GET /api/datasets
  GET /api/datasets/{dataset_id}/tasks
  GET /api/tasks/{task_id}
  GET /api/tasks/{task_id}/runs
  GET /api/tasks/{task_id}/runs/{run_tag}/memory
  GET /api/tasks/{task_id}/runs/{run_tag}/memory/summaries
  GET /api/tasks/{task_id}/runs/{run_tag}/memory/facts
"""

from __future__ import annotations

import json as _json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from evaluation.models.qa import QAPairModel
from membrain.api.schemas.viewer import (
    DatasetOut,
    FactPageItemOut,
    FactsPageOut,
    MemoryEntityOut,
    MemoryFactRefOut,
    MemoryGraphOut,
    MemoryTreeNodeOut,
    MessageOut,
    QAPairOut,
    RunTagInfo,
    SessionOut,
    SessionSummariesPageOut,
    SessionSummaryPageItemOut,
    TaskDetailOut,
    TaskOut,
)
from membrain.config import settings
from membrain.infra.db import SessionLocal
from membrain.infra.models import (
    ChatSessionModel,
    DatasetModel,
    EntityModel,
    EntityTreeNodeModel,
    FactModel,
    FactRefModel,
    TaskModel,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["viewer"])

_RUN_TAG_PATH = Path(pattern=r"^[a-zA-Z0-9_]+$")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _schema_exists(db: Session, schema_name: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
        {"s": schema_name},
    ).first()
    return row is not None


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    try:
        rows = (
            db.query(
                DatasetModel.id,
                DatasetModel.name,
                func.count(TaskModel.id).label("task_count"),
            )
            .outerjoin(TaskModel)
            .group_by(DatasetModel.id)
            .order_by(DatasetModel.id)
            .all()
        )
        return [DatasetOut(id=r.id, name=r.name, task_count=r.task_count) for r in rows]
    except SQLAlchemyError as e:
        _logger.warning("Could not fetch datasets (DB may be empty): %s", e)
        return []


@router.get("/datasets/{dataset_id}/tasks", response_model=list[TaskOut])
def list_tasks(dataset_id: int, db: Session = Depends(get_db)):
    if not db.query(DatasetModel.id).filter(DatasetModel.id == dataset_id).first():
        raise HTTPException(status_code=404, detail="Dataset not found")

    session_count = (
        select(func.count(ChatSessionModel.id))
        .where(ChatSessionModel.task_id == TaskModel.id)
        .correlate(TaskModel)
        .scalar_subquery()
        .label("session_count")
    )
    qa_count = (
        select(func.count(QAPairModel.id))
        .where(QAPairModel.task_id == TaskModel.id)
        .correlate(TaskModel)
        .scalar_subquery()
        .label("qa_count")
    )
    rows = (
        db.query(TaskModel.id, TaskModel.task_id, session_count, qa_count)
        .filter(TaskModel.dataset_id == dataset_id)
        .all()
    )
    return [
        TaskOut(
            id=r.id,
            task_id=r.task_id,
            session_count=r.session_count,
            qa_count=r.qa_count,
        )
        for r in rows
    ]


@router.get("/tasks/{task_id}", response_model=TaskDetailOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = (
        db.query(TaskModel)
        .options(
            selectinload(TaskModel.sessions).selectinload(ChatSessionModel.messages)
        )
        .filter(TaskModel.id == task_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    qa_rows = (
        db.query(QAPairModel)
        .filter(QAPairModel.task_id == task_id)
        .order_by(QAPairModel.id)
        .all()
    )
    sessions = [
        SessionOut(
            id=s.id,
            session_number=s.session_number,
            session_time=s.session_time,
            session_time_raw=s.session_time_raw,
            messages=[
                MessageOut(
                    id=m.id,
                    position=m.position,
                    dia_id=f"S{s.session_number}:{m.position}",
                    speaker=m.speaker,
                    content=m.content,
                    message_time=m.message_time,
                    message_time_raw=m.message_time_raw,
                )
                for m in s.messages
            ],
        )
        for s in task.sessions
    ]
    qa_pairs = [
        QAPairOut(
            id=q.id,
            question_id=q.question_id,
            question=q.question,
            answer=q.answer,
            category=q.category,
            evidence=[s.strip() for s in (q.evidence or "").split(",") if s.strip()],
            options=_json.loads(q.options) if q.options else None,
            reasoning=q.reasoning,
        )
        for q in qa_rows
    ]
    return TaskDetailOut(
        id=task.id, task_id=task.task_id, sessions=sessions, qa_pairs=qa_pairs
    )


@router.get("/tasks/{task_id}/runs/{run_tag}/memory", response_model=MemoryGraphOut)
def get_task_memory(
    task_id: int, run_tag: str = _RUN_TAG_PATH, db: Session = Depends(get_db)
):
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    schema = f"task_{int(task_id)}__{run_tag}"
    if not _schema_exists(db, schema):
        return MemoryGraphOut(entities=[], fact_refs=[], tree_nodes=[])

    db.execute(text(f"SET LOCAL search_path TO {schema}, public"))
    entities = db.query(EntityModel).filter(EntityModel.task_id == task_id).all()
    fact_refs = db.query(FactRefModel).all()
    entity_tree_nodes = (
        db.query(EntityTreeNodeModel)
        .filter(EntityTreeNodeModel.task_id == task_id)
        .all()
    )

    tree_fact_ids = {n.fact_id for n in entity_tree_nodes if n.fact_id is not None}
    ref_fact_ids = {r.fact_id for r in fact_refs}
    facts_by_id: dict[int, FactPageItemOut] = {}
    all_fact_ids = tree_fact_ids | ref_fact_ids
    if all_fact_ids:
        for f in db.query(FactModel).filter(FactModel.id.in_(all_fact_ids)).all():
            facts_by_id[f.id] = FactPageItemOut(
                id=f.id,
                text=f.text,
                batch_index=f.batch_index,
                fact_ts=f.fact_ts,
                status=f.status,
            )

    aliases_by_entity_id: dict[str, list[str]] = defaultdict(list)
    for r in fact_refs:
        if r.alias_text not in aliases_by_entity_id[r.entity_id]:
            aliases_by_entity_id[r.entity_id].append(r.alias_text)

    # Track per-entity which fact IDs are covered by tree nodes.
    # A fact can appear in multiple entities' fact_refs; it is an orphan for
    # entity X only when entity X has no tree node pointing to it.
    used_fact_ids_by_entity: dict[str, set[int]] = defaultdict(set)
    out_tree_nodes = []
    for n in entity_tree_nodes:
        fi = facts_by_id.get(n.fact_id) if n.fact_id else None
        if fi and n.fact_id is not None:
            used_fact_ids_by_entity[n.entity_id].add(n.fact_id)
        out_tree_nodes.append(
            MemoryTreeNodeOut(
                id=n.id,
                entity_id=n.entity_id,
                parent_id=n.parent_id,
                node_type=n.node_type,
                fact_id=n.fact_id,
                description=n.description,
                fact_text=fi.text if fi else None,
                fact_batch_index=fi.batch_index if fi else None,
                fact_ts=fi.fact_ts if fi else None,
                fact_status=fi.status if fi else None,
            )
        )

    orphans_by_entity: dict[str, list[FactPageItemOut]] = defaultdict(list)
    for r in fact_refs:
        entity_used = used_fact_ids_by_entity.get(r.entity_id, set())
        if r.fact_id not in entity_used and r.fact_id in facts_by_id:
            fi = facts_by_id[r.fact_id]
            if fi not in orphans_by_entity[r.entity_id]:
                orphans_by_entity[r.entity_id].append(fi)

    out_entities = [
        MemoryEntityOut(
            id=e.id,
            entity_id=e.entity_id,
            canonical_ref=e.canonical_ref,
            desc=e.desc,
            aliases=[
                a
                for a in aliases_by_entity_id.get(e.entity_id, [])
                if a != e.canonical_ref
            ],
            orphan_facts=orphans_by_entity.get(e.entity_id, []),
        )
        for e in entities
    ]
    out_fact_refs = [
        MemoryFactRefOut(
            fact_id=r.fact_id, entity_id=r.entity_id, alias_text=r.alias_text
        )
        for r in fact_refs
    ]
    return MemoryGraphOut(
        entities=out_entities, fact_refs=out_fact_refs, tree_nodes=out_tree_nodes
    )


@router.get(
    "/tasks/{task_id}/runs/{run_tag}/memory/summaries",
    response_model=SessionSummariesPageOut,
)
def get_task_session_summaries(
    task_id: int,
    run_tag: str = _RUN_TAG_PATH,
    limit: int = 10,
    offset: int = 0,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    schema = f"task_{int(task_id)}__{run_tag}"
    if not _schema_exists(db, schema):
        return SessionSummariesPageOut(total=0, offset=0, limit=limit, summaries=[])

    from membrain.infra.models.dataset import ChatSessionModel as CSM
    from membrain.infra.models.memory import SessionSummaryModel

    db.execute(text(f"SET LOCAL search_path TO {schema}, public"))
    q = (
        db.query(SessionSummaryModel, CSM.session_number)
        .join(CSM, SessionSummaryModel.session_id == CSM.id)
        .filter(SessionSummaryModel.task_id == task_id)
    )
    if search:
        pattern = f"%{search}%"
        q = q.filter(
            SessionSummaryModel.subject.ilike(pattern)
            | SessionSummaryModel.content.ilike(pattern)
        )
    total: int = q.with_entities(func.count(SessionSummaryModel.id)).scalar() or 0
    offset = max(0, min(offset, (total - 1) // limit * limit)) if total > 0 else 0
    rows = q.order_by(CSM.session_number).offset(offset).limit(limit).all()
    return SessionSummariesPageOut(
        total=total,
        offset=offset,
        limit=limit,
        summaries=[
            SessionSummaryPageItemOut(
                session_number=sn, subject=s.subject, content=s.content
            )
            for s, sn in rows
        ],
    )


@router.get("/tasks/{task_id}/runs/{run_tag}/memory/facts", response_model=FactsPageOut)
def get_task_facts(
    task_id: int,
    run_tag: str = _RUN_TAG_PATH,
    limit: int = 20,
    offset: int = 0,
    batch_index: int | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    schema = f"task_{int(task_id)}__{run_tag}"
    if not _schema_exists(db, schema):
        return FactsPageOut(total=0, offset=0, limit=limit, batch_options=[], facts=[])

    db.execute(text(f"SET LOCAL search_path TO {schema}, public"))
    batch_options = sorted(
        r[0]
        for r in db.query(FactModel.batch_index)
        .filter(FactModel.task_id == task_id)
        .distinct()
        .all()
        if r[0] is not None
    )
    q = db.query(FactModel).filter(FactModel.task_id == task_id)
    if batch_index is not None:
        q = q.filter(FactModel.batch_index == batch_index)
    if search:
        q = q.filter(FactModel.text.ilike(f"%{search}%"))

    total: int = q.with_entities(func.count(FactModel.id)).scalar() or 0
    offset = max(0, min(offset, (total - 1) // limit * limit)) if total > 0 else 0
    facts = q.order_by(FactModel.id).offset(offset).limit(limit).all()
    return FactsPageOut(
        total=total,
        offset=offset,
        limit=limit,
        batch_options=batch_options,
        facts=[
            FactPageItemOut(
                id=f.id,
                text=f.text,
                batch_index=f.batch_index,
                fact_ts=f.fact_ts,
                status=f.status,
            )
            for f in facts
        ],
    )


@router.get("/tasks/{task_id}/runs", response_model=list[RunTagInfo])
def list_task_runs(task_id: int, db: Session = Depends(get_db)):
    task = db.query(TaskModel).filter(TaskModel.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if settings.BACKEND_MODE == "demo":
        # Demo mode: runs live in per-task DB schemas (task_{pk}__{run_tag}).
        # Use LIKE … ESCAPE '!' to treat underscores as literals, not wildcards,
        # so task_1__% never falsely matches task_10__* schemas.
        prefix = f"task_{int(task_id)}__"
        escaped = prefix.replace("_", "!_")
        rows = db.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE :pattern ESCAPE '!'"
            ),
            {"pattern": f"{escaped}%"},
        ).fetchall()
        run_tags = [row[0][len(prefix) :] for row in rows]
    else:
        # Dev / Evaluation mode: runs are stored as exps/{run_tag}/{task_id}/ directories
        exps_dir = settings.exps_dir_path
        run_tags = []
        if exps_dir.is_dir():
            for run_dir in exps_dir.iterdir():
                if run_dir.is_dir() and (run_dir / task.task_id).is_dir():
                    run_tags.append(run_dir.name)

    return [RunTagInfo(run_tag=t) for t in sorted(run_tags)]
