"""Memory REST API — unified store / digest / search.

Endpoints:
  POST /api/memory         — store raw messages, digest pending sessions, or both
  POST /api/memory/search  — search memory for a question
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import func
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError

import membrain.retrieval.application.retrieval as _retrieval
from membrain.agents.retry import set_current_task
from membrain.api.manager import search_mgr, task_mgr
from membrain.api.schemas.memory import (
    MemoryRequest,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    RetrievedFactOut,
    RetrievedSessionOut,
)
from membrain.config import settings
from membrain.infra.db import SessionLocal
from membrain.infra.models.dataset import (
    ChatMessageModel,
    ChatSessionModel,
    DatasetModel,
    TaskModel,
)
from membrain.infra.queries.tasks import get_task_pk

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["memory"])

_RUN_TAG = "default"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_or_create_dataset_task(
    db,
    dataset_name: str,
    task_name: str,
    agent_profile: str | None = None,
) -> tuple[DatasetModel, TaskModel]:
    """Get or create Dataset + Task by name. Handles concurrent races."""
    for _ in range(2):
        dataset = db.query(DatasetModel).filter_by(name=dataset_name).first()
        if not dataset:
            try:
                dataset = DatasetModel(name=dataset_name)
                db.add(dataset)
                db.flush()
            except IntegrityError:
                db.rollback()
                dataset = db.query(DatasetModel).filter_by(name=dataset_name).first()

        task = (
            db.query(TaskModel)
            .filter_by(dataset_id=dataset.id, task_id=task_name)
            .first()
        )
        if not task:
            try:
                task = TaskModel(
                    dataset_id=dataset.id,
                    task_id=task_name,
                    agent_profile=agent_profile,
                )
                db.add(task)
                db.flush()
            except IntegrityError:
                db.rollback()
                task = (
                    db.query(TaskModel)
                    .filter_by(dataset_id=dataset.id, task_id=task_name)
                    .first()
                )

        if dataset and task:
            return dataset, task

    raise RuntimeError(
        f"Failed to get-or-create dataset={dataset_name!r} task={task_name!r}"
    )


def _load_session_messages(session_pk: int) -> list[dict]:
    with SessionLocal() as db:
        rows = (
            db.query(ChatMessageModel)
            .filter_by(session_id=session_pk)
            .order_by(ChatMessageModel.position)
            .all()
        )
        return [
            {
                "speaker": r.speaker,
                "content": r.content,
                "message_time": r.message_time_raw or "",
            }
            for r in rows
        ]


def _mark_digested(session_pk: int) -> None:
    with SessionLocal() as db:
        db.query(ChatSessionModel).filter_by(id=session_pk).update(
            {"digested_at": datetime.now(timezone.utc)}
        )
        db.commit()


# ── Background digest ─────────────────────────────────────────────────────────

_background_digest_tasks: set[asyncio.Task] = set()


async def _run_digest(task_pk: int, agent_profile: str | None) -> None:
    set_current_task(str(task_pk))
    async with task_mgr.get_lock(task_pk):
        with SessionLocal() as db:
            pending = (
                db.query(ChatSessionModel)
                .filter(
                    ChatSessionModel.task_id == task_pk,
                    ChatSessionModel.digested_at.is_(None),
                )
                .order_by(ChatSessionModel.session_number)
                .all()
            )
            pending = list(pending)
            for s in pending:
                db.expunge(s)

        if not pending:
            return

        workflow = task_mgr.get_or_create(task_pk)
        try:
            for sess in pending:
                sess_messages = _load_session_messages(sess.id)
                if not sess_messages:
                    _mark_digested(sess.id)
                    continue
                await workflow.process_session(
                    task_pk=task_pk,
                    messages=sess_messages,
                    session_number=sess.session_number,
                    session_pk=sess.id,
                    session_time=sess.session_time_raw or "",
                    profile=agent_profile,
                )
                _mark_digested(sess.id)
        except Exception:
            log.exception("background digest failed task_pk=%s", task_pk)
        finally:
            task_mgr.cleanup(task_pk)


# ── POST /api/memory ────────────────────────────────────────────────────────


@router.post("/memory", response_model=MemoryResponse)
async def process_memory(req: MemoryRequest):
    """Unified memory endpoint.

    Modes (controlled by ``store`` and ``digest``):
      store=True,  digest=False  — save raw messages only
      store=True,  digest=True   — save then digest all pending sessions
      store=False, digest=True   — digest all pending sessions (no new data)
    """
    messages = [m.model_dump() for m in req.messages]

    if req.store and not messages:
        raise HTTPException(400, "messages required when store=True")
    if not req.store and not req.digest:
        raise HTTPException(400, "at least one of store or digest must be True")

    # ── Resolve dataset / task ───────────────────────────────────────
    with SessionLocal() as db:
        dataset, task = _get_or_create_dataset_task(
            db,
            req.dataset,
            req.task,
            req.agent_profile,
        )
        dataset_id = dataset.id
        task_pk = task.id
        agent_profile = task.agent_profile
        db.commit()

    # ── Store ────────────────────────────────────────────────────────
    session_pk: int | None = None
    session_number: int | None = None

    if req.store:
        with SessionLocal() as db:
            max_sn = (
                db.query(func.max(ChatSessionModel.session_number))
                .filter_by(task_id=task_pk)
                .scalar()
            ) or 0
            session_number = max_sn + 1

            session_dt = None
            if req.session_time:
                try:
                    session_dt = datetime.fromisoformat(req.session_time)
                except ValueError:
                    pass

            session = ChatSessionModel(
                task_id=task_pk,
                session_number=session_number,
                session_time=session_dt,
                session_time_raw=req.session_time or None,
                digested_at=None,
            )
            db.add(session)
            db.flush()
            session_pk = session.id

            for pos, msg in enumerate(messages):
                msg_dt = None
                if msg.get("message_time"):
                    try:
                        msg_dt = datetime.fromisoformat(msg["message_time"])
                    except ValueError:
                        pass
                db.add(
                    ChatMessageModel(
                        session_id=session_pk,
                        position=pos,
                        speaker=msg["speaker"],
                        content=msg["content"],
                        message_time=msg_dt,
                        message_time_raw=msg.get("message_time") or None,
                    )
                )
            db.commit()

    # ── Digest (async background) ────────────────────────────────────
    if req.digest:
        t = asyncio.create_task(_run_digest(task_pk, agent_profile))
        _background_digest_tasks.add(t)
        t.add_done_callback(_background_digest_tasks.discard)

    # ── Response ─────────────────────────────────────────────────────
    if req.store and req.digest:
        status = "stored_and_digest_queued"
    elif req.digest:
        status = "digest_queued"
    else:
        status = "stored"

    return MemoryResponse(
        dataset_id=dataset_id,
        task_pk=task_pk,
        session_id=session_pk,
        session_number=session_number,
        digested_sessions=0,
        status=status,
    )


# ── POST /api/memory/search ───────────────────────────────────────────────


@router.post("/memory/search", response_model=MemorySearchResponse)
async def search_memory(req: MemorySearchRequest):
    """Search memory for a question, returning packed context."""
    resolved = get_task_pk(req.dataset, req.task)
    if resolved is None:
        raise HTTPException(
            404, f"Task '{req.task}' not found in dataset '{req.dataset}'"
        )
    task_pk = resolved

    sf = search_mgr.get_session_factory()
    embed_client = search_mgr.get_embed_client()
    http_client = search_mgr.get_http_client()
    top_k = req.top_k or settings.QA_RERANK_TOP_K

    schema = f"task_{int(task_pk)}__{_RUN_TAG}"
    with sf() as db:
        db.execute(sa_text(f"SET LOCAL search_path TO {schema}, public"))
        result = _retrieval.search(
            question=req.question,
            task_id=task_pk,
            db=db,
            embed_client=embed_client,
            http_client=http_client,
            top_k=top_k,
            strategy=req.strategy,
            mode=req.mode,
        )

    return MemorySearchResponse(
        packed_context=result["packed_context"],
        packed_token_count=result["packed_token_count"],
        fact_ids=result["fact_ids"],
        facts=[RetrievedFactOut(**f) for f in result["facts"]],
        sessions=[RetrievedSessionOut(**s) for s in result["sessions"]],
        raw_messages=[],
    )
