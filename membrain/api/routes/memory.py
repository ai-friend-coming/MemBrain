"""Memory REST API — unified store / digest / search.

Endpoints:
  POST /api/memory         — store raw messages, digest pending sessions, or both
  GET  /api/memory/jobs/*  — inspect durable asynchronous digest results
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
    MemoryJobResponse,
    MemoryRequest,
    MemoryResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    RetrievedFactOut,
    RetrievedSessionOut,
)
from membrain.api.trace import finish_trace, start_trace
from membrain.config import settings
from membrain.infra.db import SessionLocal
from membrain.infra.models.dataset import (
    ChatMessageModel,
    ChatSessionModel,
    DatasetModel,
    MemoryDigestJobModel,
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

_background_digest_tasks: dict[str, asyncio.Task] = {}
_legacy_digest_tasks: set[asyncio.Task] = set()


async def _run_digest(
    task_pk: int,
    agent_profile: str | None,
    session_pk: int | None = None,
) -> tuple[int, str]:
    """处理指定任务下的待 digest 会话并返回完成数量。

    Args:
        task_pk: 内部任务主键。
        agent_profile: 任务级 Agent 画像。

    Returns:
        tuple[int, str]: 本次完成数量以及空字符串或最终异常。
    """
    set_current_task(str(task_pk))
    digested_sessions = 0
    async with task_mgr.get_lock(task_pk):
        with SessionLocal() as db:
            query = db.query(ChatSessionModel).filter(
                ChatSessionModel.task_id == task_pk,
                ChatSessionModel.digested_at.is_(None),
            )
            if session_pk is not None:
                query = query.filter(ChatSessionModel.id == session_pk)
            pending = query.order_by(ChatSessionModel.session_number).all()
            pending = list(pending)
            for session in pending:
                db.expunge(session)

        if not pending:
            return 0, ""

        workflow = task_mgr.get_or_create(task_pk)
        try:
            for session in pending:
                session_messages = _load_session_messages(session.id)
                if not session_messages:
                    _mark_digested(session.id)
                    digested_sessions += 1
                    continue
                await workflow.process_session(
                    task_pk=task_pk,
                    messages=session_messages,
                    session_number=session.session_number,
                    session_pk=session.id,
                    session_time=session.session_time_raw or "",
                    profile=agent_profile,
                )
                _mark_digested(session.id)
                digested_sessions += 1
        except Exception as exc:
            log.exception("background digest failed task_pk=%s", task_pk)
            return digested_sessions, f"{type(exc).__name__}: {exc}"
        finally:
            task_mgr.cleanup(task_pk)
    return digested_sessions, ""


def _job_response(job: MemoryDigestJobModel) -> MemoryJobResponse:
    """把持久化任务转换成稳定的查询响应。"""
    with SessionLocal() as db:
        task = db.query(TaskModel).filter_by(id=job.task_id).one()
        session = db.query(ChatSessionModel).filter_by(id=job.session_id).one()
        return MemoryJobResponse(
            request_id=job.request_id,
            dataset_id=task.dataset_id,
            task_pk=task.id,
            session_id=session.id,
            session_number=session.session_number,
            status=job.status,
            digested_sessions=job.digested_sessions,
            trace=job.trace,
            error=job.error,
        )


def _memory_response_from_job(job: MemoryDigestJobModel) -> MemoryResponse:
    """为幂等重提返回现有任务，而不重复创建原始会话。"""
    result = _job_response(job)
    if result.status == "succeeded":
        status = "stored_and_digested"
    elif result.status == "failed":
        status = "stored_and_digest_failed"
    else:
        status = "stored_and_digest_queued"
    return MemoryResponse(
        dataset_id=result.dataset_id,
        task_pk=result.task_pk,
        session_id=result.session_id,
        session_number=result.session_number,
        digested_sessions=result.digested_sessions,
        status=status,
        trace=result.trace,
        request_id=result.request_id,
    )


def _load_job(request_id: str) -> MemoryDigestJobModel | None:
    """读取脱离数据库会话的任务快照。"""
    with SessionLocal() as db:
        job = db.query(MemoryDigestJobModel).filter_by(request_id=request_id).first()
        if job is not None:
            db.expunge(job)
        return job


def _save_job_trace(request_id: str, trace: dict) -> None:
    """在每次上游调用后保存当前累计 trace。"""
    with SessionLocal() as db:
        db.query(MemoryDigestJobModel).filter_by(request_id=request_id).update(
            {"trace": trace, "updated_at": datetime.now(timezone.utc)}
        )
        db.commit()


async def _run_digest_job(request_id: str) -> None:
    """执行一条持久化任务，并用 task 级数据库锁隔离多 worker。"""
    job = _load_job(request_id)
    if job is None or job.status in {"succeeded", "failed"}:
        return

    # 同一个 task 的派生表不能被多个进程同时更新；未拿到锁时异步等待，不阻塞 API event loop。
    lock_db = None
    acquired = False
    trace = None
    try:
        while not acquired:
            candidate = SessionLocal()
            try:
                acquired = bool(
                    candidate.execute(
                        sa_text(
                            "SELECT pg_try_advisory_lock("
                            "hashtextextended(:lock_key, 0))"
                        ),
                        {"lock_key": f"memory-digest-task:{job.task_id}"},
                    ).scalar()
                )
            except Exception:
                candidate.close()
                raise
            if acquired:
                lock_db = candidate
            else:
                candidate.close()
                await asyncio.sleep(1)

        job = _load_job(request_id)
        if job is None or job.status in {"succeeded", "failed"}:
            return

        with SessionLocal() as db:
            task = db.query(TaskModel).filter_by(id=job.task_id).one()
            agent_profile = task.agent_profile
            db.query(MemoryDigestJobModel).filter_by(request_id=request_id).update(
                {"status": "running", "updated_at": datetime.now(timezone.utc)}
            )
            db.commit()

        trace, trace_token = start_trace(
            initial_trace=job.trace,
            on_change=lambda snapshot: _save_job_trace(request_id, snapshot),
        )
        try:
            digested_sessions, digest_error = await _run_digest(
                job.task_id,
                agent_profile,
                job.session_id,
            )
            finished_at = datetime.now(timezone.utc)
            with SessionLocal() as db:
                db.query(MemoryDigestJobModel).filter_by(
                    request_id=request_id
                ).update(
                    {
                        "status": "failed" if digest_error else "succeeded",
                        "digested_sessions": digested_sessions,
                        "trace": trace.snapshot(),
                        "error": digest_error,
                        "updated_at": finished_at,
                        "completed_at": finished_at,
                    }
                )
                db.commit()
        finally:
            finish_trace(trace_token)
    except asyncio.CancelledError:
        # 关闭时保留 running，下一次 API 启动会继续恢复同一 request_id。
        raise
    except Exception as exc:
        log.exception("durable digest job failed request_id=%s", request_id)
        finished_at = datetime.now(timezone.utc)
        try:
            with SessionLocal() as db:
                db.query(MemoryDigestJobModel).filter_by(
                    request_id=request_id
                ).update(
                    {
                        "status": "failed",
                        "trace": trace.snapshot() if trace is not None else job.trace,
                        "error": f"{type(exc).__name__}: {exc}",
                        "updated_at": finished_at,
                        "completed_at": finished_at,
                    }
                )
                db.commit()
        except Exception:
            log.exception(
                "persist durable digest failure failed request_id=%s", request_id
            )
    finally:
        if acquired and lock_db is not None:
            lock_db.execute(
                sa_text(
                    "SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"
                ),
                {"lock_key": f"memory-digest-task:{job.task_id}"},
            )
            lock_db.close()


def _schedule_digest_job(request_id: str) -> None:
    """保证当前进程内同一持久化任务最多只有一个执行协程。"""
    existing = _background_digest_tasks.get(request_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_run_digest_job(request_id))
    _background_digest_tasks[request_id] = task

    def discard(completed: asyncio.Task, key: str = request_id) -> None:
        """移除当前进程任务引用，并读取异常避免静默丢失。"""
        if _background_digest_tasks.get(key) is completed:
            _background_digest_tasks.pop(key, None)
        if not completed.cancelled() and completed.exception() is not None:
            log.error(
                "durable digest task exited unexpectedly request_id=%s: %s",
                key,
                completed.exception(),
            )

    task.add_done_callback(discard)


def resume_memory_digest_jobs() -> None:
    """在 API 启动后恢复排队或因进程退出而中断的任务。"""
    with SessionLocal() as db:
        request_ids = [
            row[0]
            for row in db.query(MemoryDigestJobModel.request_id)
            .filter(MemoryDigestJobModel.status.in_(("queued", "running")))
            .all()
        ]
    for request_id in request_ids:
        _schedule_digest_job(request_id)


async def shutdown_memory_digest_jobs() -> None:
    """取消当前进程任务；数据库中的 running 状态由下次启动恢复。"""
    tasks = list(_background_digest_tasks.values()) + list(_legacy_digest_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_digest_tasks.clear()
    _legacy_digest_tasks.clear()


# ── POST /api/memory ────────────────────────────────────────────────────────


@router.post("/memory", response_model=MemoryResponse)
async def process_memory(req: MemoryRequest):
    """存储带聊天来源的消息，并按需等待记忆抽取完成。

    Args:
        req: 包含必填 chat_id、消息和存储模式的请求参数。

    Returns:
        MemoryResponse: 新建内部会话、digest 状态和当前请求 trace。
    """
    trace, trace_token = start_trace()
    try:
        messages = [m.model_dump() for m in req.messages]

        if req.request_id:
            if not req.store or not req.digest or req.wait_for_digest:
                raise HTTPException(
                    400,
                    "request_id requires store=True, digest=True, wait_for_digest=False",
                )
            existing_job = _load_job(req.request_id)
            if existing_job is not None:
                return _memory_response_from_job(existing_job)

        if req.store and not messages:
            raise HTTPException(400, "messages required when store=True")
        if not req.store and not req.digest:
            raise HTTPException(400, "at least one of store or digest must be True")

        # dataset 和 task 是事实、实体、会话摘要共同使用的隔离边界。
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

        session_pk: int | None = None
        session_number: int | None = None

        if req.store:
            try:
                with SessionLocal() as db:
                    # task 内的 session_number 必须跨 API worker 串行分配。
                    db.execute(
                        sa_text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtextextended(:lock_key, 0))"
                        ),
                        {"lock_key": f"memory-session-task:{task_pk}"},
                    )
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
                        chat_id=req.chat_id,
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

                    if req.request_id:
                        db.add(
                            MemoryDigestJobModel(
                                request_id=req.request_id,
                                task_id=task_pk,
                                session_id=session_pk,
                                status="queued",
                                trace={
                                    "duration_ms": 0.0,
                                    "calls": [],
                                    "total_usage": {
                                        "prompt_tokens": 0,
                                        "completion_tokens": 0,
                                        "total_tokens": 0,
                                    },
                                    "estimated_cost_usd": None,
                                },
                            )
                        )
                    db.commit()
            except IntegrityError:
                # 同一个幂等键并发提交时，失败事务包含的 session 会一起回滚。
                if req.request_id:
                    existing_job = _load_job(req.request_id)
                    if existing_job is not None:
                        return _memory_response_from_job(existing_job)
                raise

        digested_sessions = 0
        digest_error = ""
        if req.request_id:
            _schedule_digest_job(req.request_id)
        elif req.digest and req.wait_for_digest:
            # 同步等待让本次 response 能包含 digest 内全部上游调用 usage。
            digested_sessions, digest_error = await _run_digest(
                task_pk, agent_profile
            )
        elif req.digest:
            background_task = asyncio.create_task(_run_digest(task_pk, agent_profile))
            _legacy_digest_tasks.add(background_task)
            background_task.add_done_callback(_legacy_digest_tasks.discard)

        if req.wait_for_digest and req.digest and digest_error:
            status = "stored_and_digest_failed" if req.store else "digest_failed"
        elif req.store and req.digest and req.wait_for_digest:
            status = "stored_and_digested"
        elif req.digest and req.wait_for_digest:
            status = "digested"
        elif req.store and req.digest:
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
            digested_sessions=digested_sessions,
            status=status,
            trace=trace.snapshot(),
            request_id=req.request_id,
        )
    finally:
        finish_trace(trace_token)


@router.get("/memory/jobs/{request_id}", response_model=MemoryJobResponse)
async def get_memory_job(request_id: str):
    """查询持久化异步 digest 的当前或最终结果。"""
    job = _load_job(request_id)
    if job is None:
        raise HTTPException(404, "memory digest job not found")
    return _job_response(job)


@router.post("/memory/jobs/{request_id}/retry", response_model=MemoryJobResponse)
async def retry_memory_job(request_id: str):
    """原地重试失败任务，并复用已保存的原始会话。"""
    with SessionLocal() as db:
        job = (
            db.query(MemoryDigestJobModel)
            .filter_by(request_id=request_id)
            .with_for_update()
            .first()
        )
        if job is None:
            raise HTTPException(404, "memory digest job not found")
        if job.status == "failed":
            job.status = "queued"
            job.error = ""
            job.completed_at = None
            job.updated_at = datetime.now(timezone.utc)
            db.commit()
        db.expunge(job)
    if job.status in {"queued", "running"}:
        _schedule_digest_job(request_id)
    return _job_response(job)


# ── POST /api/memory/search ───────────────────────────────────────────────


@router.post("/memory/search", response_model=MemorySearchResponse)
async def search_memory(req: MemorySearchRequest):
    """检索记忆并返回结果及本次请求的完整上游 trace。

    Args:
        req: 数据隔离键、问题和召回策略。

    Returns:
        MemorySearchResponse: 召回结果、聊天来源和临时 trace。
    """
    trace, trace_token = start_trace()
    try:
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
            trace=trace.snapshot(),
        )
    finally:
        finish_trace(trace_token)
