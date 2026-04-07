"""Memory ingestion pipeline core logic.

The CLI entry point is ``evaluation.cli.exp_main`` (``uv run exp``).
This module exposes ``run_pipeline()`` and helper functions for direct import.
"""

import asyncio
import logging
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from sqlalchemy.orm import selectinload  # noqa: E402

from evaluation.ingest.adapters import REGISTRY as ADAPTER_REGISTRY  # noqa: E402
from evaluation.runtime.local_runner import LocalRunner  # noqa: E402
from evaluation.ui.memory_console import _display_rich_ui  # noqa: E402
from evaluation.utils.tasks import pipeline_log  # noqa: E402
from membrain.config import settings  # noqa: E402
from membrain.infra.checkpoint import (  # noqa: E402
    clear_ingestion_tables,
    delete_checkpoint,
    list_run_tasks,
    mark_done,
    purge_task_memory,
    save_run_meta,
)
from membrain.infra.db import SessionLocal  # noqa: E402
from membrain.infra.models.dataset import (  # noqa: E402
    ChatSessionModel,
    DatasetModel,
    TaskModel,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MAX_TASK_RETRIES = 2  # total attempts per task = MAX_TASK_RETRIES + 1
log = logging.getLogger(__name__)


# ── Per-task file logging ─────────────────────────────────────────────────────


class _ThreadFilter(logging.Filter):
    """Only pass log records emitted from a specific thread."""

    def __init__(self, thread_id: int) -> None:
        self._tid = thread_id

    def filter(self, record: logging.LogRecord) -> bool:
        return threading.get_ident() == self._tid


def _setup_task_log(run_tag: str, task_id: str) -> logging.FileHandler:
    log_dir = settings.exps_dir_path / run_tag / task_id
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / f"{task_id}.log", encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.addFilter(_ThreadFilter(threading.get_ident()))
    logging.getLogger().addHandler(handler)
    return handler


# ── Helpers ──────────────────────────────────────────────────────────────────


def get_task_pk(dataset: str, task_id: str) -> int:
    with SessionLocal() as db:
        row = (
            db.query(TaskModel.id)
            .join(DatasetModel)
            .filter(DatasetModel.name == dataset, TaskModel.task_id == task_id)
            .first()
        )
        if not row:
            raise ValueError(f"Task '{task_id}' not found in dataset '{dataset}'")
        return row[0]


def get_all_task_ids(dataset: str) -> list[str]:
    """Return all task_id strings for a dataset."""
    with SessionLocal() as db:
        rows = (
            db.query(TaskModel.task_id)
            .join(DatasetModel)
            .filter(DatasetModel.name == dataset)
            .order_by(TaskModel.id)
            .all()
        )
        return [r[0] for r in rows]


def load_session_messages(
    dataset: str, task_id: str, session_number: int
) -> list[dict]:
    with SessionLocal() as db:
        session = (
            db.query(ChatSessionModel)
            .join(TaskModel)
            .join(DatasetModel)
            .filter(
                DatasetModel.name == dataset,
                TaskModel.task_id == task_id,
                ChatSessionModel.session_number == session_number,
            )
            .options(selectinload(ChatSessionModel.messages))
            .first()
        )
        if not session:
            return []
        return [
            {
                "speaker": msg.speaker,
                "content": msg.content,
                "message_time": (
                    msg.message_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if msg.message_time
                    else ""
                ),
            }
            for msg in session.messages
        ]


def get_session_count(dataset: str, task_id: str) -> int:
    with SessionLocal() as db:
        return (
            db.query(ChatSessionModel)
            .join(TaskModel)
            .join(DatasetModel)
            .filter(
                DatasetModel.name == dataset,
                TaskModel.task_id == task_id,
            )
            .count()
        )


def _get_session_pk(dataset: str, task_id: str, session_number: int) -> int | None:
    """Return the PK (chat_sessions.id) for a given session number."""
    with SessionLocal() as db:
        row = (
            db.query(ChatSessionModel.id)
            .join(TaskModel)
            .join(DatasetModel)
            .filter(
                DatasetModel.name == dataset,
                TaskModel.task_id == task_id,
                ChatSessionModel.session_number == session_number,
            )
            .first()
        )
        return row[0] if row else None


# ── Pipeline run tracking (filesystem-based) ─────────────────────────────────


def _run_tag_exists(run_tag: str) -> bool:
    return (settings.exps_dir_path / run_tag).is_dir()


def _list_runs_local() -> list[tuple[str, dict[str, int]]]:
    """Scan exps/ directory and report run status per run_tag."""
    exps_dir = settings.exps_dir_path
    if not exps_dir.is_dir():
        return []
    result = {}
    for run_dir in sorted(exps_dir.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith("__"):
            continue
        run_tag = run_dir.name
        counts = {"task_count": 0, "completed": 0, "incomplete": 0}
        for task_dir in run_dir.iterdir():
            if not task_dir.is_dir() or task_dir.name in ("logs", "qa_logs"):
                continue
            counts["task_count"] += 1
            if (task_dir / "ckpts" / "done").exists():
                counts["completed"] += 1
            else:
                counts["incomplete"] += 1
        result[run_tag] = counts
    return sorted(result.items())


def _delete_run_local(run_tag: str) -> int:
    """Drop all schemas and purge memory data for a run_tag."""
    import subprocess

    from membrain.infra.checkpoint import _CONTAINER_EXPS, _PG_CONTAINER

    task_ids = list_run_tasks(run_tag)
    if task_ids:
        with SessionLocal() as db:
            rows = db.query(TaskModel.id).filter(TaskModel.task_id.in_(task_ids)).all()
        task_pks = [r[0] for r in rows]
    else:
        task_pks = []

    count = 0
    for pk in task_pks:
        purge_task_memory(pk, run_tag)
        count += 1

    result = subprocess.run(
        ["docker", "exec", _PG_CONTAINER, "rm", "-rf", f"{_CONTAINER_EXPS}/{run_tag}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        log.warning(
            "docker rm -rf for run '%s' exited with code %d: %s",
            run_tag,
            result.returncode,
            result.stderr,
        )
    return count


# ── RunState + global event log ──────────────────────────────────────────────


@dataclass
class RunState:
    """Shared state between the pipeline orchestrator and display thread."""

    run_tag: str
    total_tasks: int
    # Worker threads write to their own task_id slot only (no cross-task contention).
    # The display thread reads all slots. Both rely on CPython's GIL for safety.
    progress: dict  # task_id → progress entry; workers write, display reads
    log_buffer: deque  # maxlen=3; recent milestone messages for terminal panel
    log_lock: threading.Lock
    log_file: IO[str]  # open file handle for exps/{run_tag}/logs/run.log
    pass1_start: float  # time.monotonic() when run starts (for Elapsed display)
    pass1_rate_start: (
        float | None
    )  # set on first Pass 1 session completion (for ETA rate)
    pass2_start: float | None  # set once when the first task enters ingest phase


global_log = pipeline_log


# ── Worker thread entry point ────────────────────────────────────────────────


def _worker_run_task(
    dataset: str,
    task_id: str,
    task_pk: int,
    max_sessions: int | None,
    start_session: int,
    resume: bool,
    run_tag: str,
    state: RunState,
    summary_only: bool = False,
    regen_summary: bool = False,
    regen_ingestion: bool = False,
) -> dict:
    """Run a single task in a worker thread via the in-process session workflow.

    Each worker creates its own ``LocalRunner`` with an isolated DB engine.
    Retries up to MAX_TASK_RETRIES times on failure.
    """
    for _lib in ("httpx", "httpcore"):
        logging.getLogger(_lib).setLevel(logging.WARNING)

    log_handler = _setup_task_log(run_tag, task_id)
    runner: LocalRunner | None = None
    try:
        adapter_cls = ADAPTER_REGISTRY.get(dataset)
        adapter = adapter_cls()
        n_sessions = get_session_count(dataset, task_id)
        sessions_to_process = (
            n_sessions if max_sessions is None else min(n_sessions, max_sessions)
        )

        state.progress[task_id].update(
            {
                "status": "running",
                "phase": "summary",
                "done_summary": 0,
                "total_summary": sessions_to_process,
                "summary_base": 0,  # sessions skipped on Pass 1 resume
                "done_ingest": 0,
                "total_ingest": sessions_to_process,
                "ingest_base": 0,  # sessions skipped on Pass 2 resume
            }
        )

        # ── Retry loop ──
        attempt = 0
        last_error = ""
        while attempt <= MAX_TASK_RETRIES:
            resume_this = resume or (attempt > 0)
            try:
                runner = LocalRunner(task_pk, run_tag)
                _process_task(
                    runner,
                    dataset,
                    task_id,
                    task_pk,
                    run_tag,
                    adapter,
                    sessions_to_process,
                    start_session,
                    resume=resume_this,
                    state=state,
                    summary_only=summary_only,
                    regen_summary=regen_summary,
                    regen_ingestion=regen_ingestion,
                )
                if not summary_only:
                    mark_done(task_pk, run_tag)
                state.progress[task_id]["status"] = "completed"
                global_log(state, f"[{task_id}] completed")
                return {"task_id": task_id, "status": "completed", "run_tag": run_tag}

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                attempt += 1
                log.error(
                    "task %s attempt %d failed:\n%s",
                    task_id,
                    attempt,
                    traceback.format_exc(),
                )
                if attempt <= MAX_TASK_RETRIES:
                    global_log(
                        state,
                        f"[{task_id}] retry {attempt}/{MAX_TASK_RETRIES}  {last_error}",
                    )
                    time.sleep(2**attempt)

            finally:
                if runner is not None:
                    runner.cleanup()
                    runner = None

        state.progress[task_id]["status"] = "failed"
        global_log(state, f"[{task_id}] FAILED  {last_error}")
        return {
            "task_id": task_id,
            "status": "failed",
            "error": last_error,
            "run_tag": run_tag,
        }

    finally:
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()


def _process_task(
    runner: LocalRunner,
    dataset: str,
    task_id: str,
    task_pk: int,
    run_tag: str,
    adapter,
    sessions_to_process: int,
    start_session: int,
    resume: bool,
    state: RunState,
    summary_only: bool = False,
    regen_summary: bool = False,
    regen_ingestion: bool = False,
) -> None:
    """Process sessions via the in-process session workflow (no HTTP).

    Pass 1: summarize all sessions sequentially.
    Pass 2: ingest all sessions sequentially (skipping summarize).
    """
    resume_pass: int | None = None
    resume_session: int | None = None
    _force_skip_pass1 = False

    if regen_summary:
        runner.clear_all_summaries()
        delete_checkpoint(task_pk, run_tag)
        resume = False

    _was_resume = resume
    if resume or regen_ingestion:
        meta = runner.load_checkpoint()
        if meta is not None and meta.get("session_number") is not None:
            resume_pass = meta["pass"]
            resume_session = meta["session_number"]
            _pass1_fully_done = (
                resume_pass == 1 and resume_session >= sessions_to_process
            )
            if not _pass1_fully_done:
                runner.restore_from_checkpoint(meta)
            log.info(
                "[%s] resuming from pass=%s session=%s",
                task_id,
                resume_pass,
                resume_session,
            )
            if regen_ingestion:
                _pass1_done = resume_pass == 2 or (
                    resume_pass == 1 and resume_session >= sessions_to_process
                )
                if _pass1_done:
                    # (b): Pass 1 complete — wipe Pass 2 data and restart it
                    runner.clear_ingestion_data()
                    runner.save_summary_checkpoint(sessions_to_process)
                    resume = False
                    resume_pass = None
                    resume_session = None
                    _force_skip_pass1 = True
                else:
                    # (a): Pass 1 not done — resume Pass 1 from checkpoint,
                    # then start Pass 2 fresh (no Pass 2 data exists yet).
                    resume = True
        else:
            if _was_resume:
                clear_ingestion_tables(task_pk, run_tag)
                log.warning(
                    "[%s] resume requested but no checkpoint found; "
                    "cleared Pass-2 tables, restarting from scratch",
                    task_id,
                )
                resume = False
            # regen_ingestion with no checkpoint: run both passes from scratch

    loop = asyncio.new_event_loop()
    try:
        # ── Pass 1: summarize ──────────────────────────────────────────
        skip_pass1 = _force_skip_pass1 or (resume and resume_pass == 2)
        state.progress[task_id]["phase"] = "summary"
        if not skip_pass1:
            pass1_start = start_session
            if resume and resume_pass == 1 and resume_session is not None:
                pass1_start = resume_session + 1
                already_done = max(0, resume_session - start_session + 1)
                state.progress[task_id]["done_summary"] = already_done
                state.progress[task_id]["summary_base"] = already_done
            else:
                # Starting Pass 1 from scratch — reset counter in case this is a retry
                state.progress[task_id]["done_summary"] = 0
                state.progress[task_id]["summary_base"] = 0
            log.info(
                "[%s] Pass 1: summarizing sessions %d–%d",
                task_id,
                pass1_start,
                sessions_to_process,
            )
            for sess_num in range(pass1_start, sessions_to_process + 1):
                messages = load_session_messages(dataset, task_id, sess_num)
                if not messages:
                    state.progress[task_id]["done_summary"] += 1
                    continue
                session_pk = _get_session_pk(dataset, task_id, sess_num)
                if session_pk is None:
                    state.progress[task_id]["done_summary"] += 1
                    continue
                session_time = messages[0].get("message_time", "")
                if state.pass1_rate_start is None:
                    state.pass1_rate_start = time.monotonic()
                loop.run_until_complete(
                    runner.summarize_session_only(
                        messages=adapter.prepare_summary_messages(messages),
                        session_number=sess_num,
                        session_pk=session_pk,
                        session_time=session_time,
                    )
                )  # raises on failure → task-level retry without advancing checkpoint
                log.info("[%s] summarize session %d: ok", task_id, sess_num)
                runner.save_summary_checkpoint(sess_num)
                state.progress[task_id]["done_summary"] += 1
        else:
            log.info("[%s] Pass 1: skipped (summaries in checkpoint)", task_id)
            state.progress[task_id]["done_summary"] = sessions_to_process

        if summary_only:
            return

        # ── Pass 2: ingest ─────────────────────────────────────────────
        state.progress[task_id]["phase"] = "ingest"
        pass2_sess_start = start_session
        if resume and resume_pass == 2 and resume_session is not None:
            pass2_sess_start = resume_session + 1
        log.info(
            "[%s] Pass 2: ingesting sessions %d–%d",
            task_id,
            pass2_sess_start,
            sessions_to_process,
        )

        # Fast-forward through already-completed sessions (no actual work).
        # Set ingest_base and pass2_start AFTER this skip so the ETA rate
        # is computed only from sessions that required real processing.
        sessions_done = 0
        for sess_num in range(start_session, pass2_sess_start):
            sessions_done += 1
            state.progress[task_id]["done_ingest"] = sessions_done

        state.progress[task_id]["ingest_base"] = sessions_done

        for sess_num in range(pass2_sess_start, sessions_to_process + 1):
            messages = load_session_messages(dataset, task_id, sess_num)
            if not messages:
                log.info("[%s] session %d: no messages, skipping", task_id, sess_num)
                sessions_done += 1
                state.progress[task_id]["done_ingest"] = sessions_done
                continue

            session_pk = _get_session_pk(dataset, task_id, sess_num)
            if session_pk is None:
                log.info("[%s] session %d: PK not found, skipping", task_id, sess_num)
                sessions_done += 1
                state.progress[task_id]["done_ingest"] = sessions_done
                continue

            summary = runner.get_session_summary(session_pk)
            messages_to_ingest = adapter.prepare_ingest_messages(messages, summary)

            log.info(
                "[%s] session %d: %d msg(s)",
                task_id,
                sess_num,
                len(messages_to_ingest),
            )
            if state.pass2_start is None:
                state.pass2_start = time.monotonic()
            result = loop.run_until_complete(
                runner.ingest_session(
                    messages=messages_to_ingest,
                    session_number=sess_num,
                )
            )
            log.info(
                "[%s] session %d: %d batches, %d entities, %d facts",
                task_id,
                sess_num,
                result.batches_processed,
                result.entity_count,
                result.fact_count,
            )
            sessions_done += 1
            state.progress[task_id]["done_ingest"] = sessions_done
    finally:
        loop.close()


# ── Progress display (multi-task) ────────────────────────────────────────────
# _format_eta and _display_rich_ui are imported from evaluation.ui.memory_console


# ── Pipeline entry point ─────────────────────────────────────────────────────


def run_pipeline(
    dataset: str,
    task_ids: list[str],
    task_pks: dict[str, int],
    run_tag: str,
    max_sessions: int | None = None,
    start_session: int = 1,
    max_workers: int = 1,
    resume: bool = False,
    summary_only: bool = False,
    regen_summary: bool = False,
    regen_ingestion: bool = False,
) -> int:
    """Execute the memory ingestion pipeline (no CLI layer).

    Returns 0 on success, 1 if any tasks failed.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not resume:
        save_run_meta(run_tag, dataset)

    print("Mode         : local (in-process session workflow)")
    print(f"Dataset      : {dataset}")
    print(f"Run tag      : {run_tag}")
    print(f"Tasks ({len(task_ids)}): {task_ids}")

    _max_workers = min(max_workers, len(task_ids))
    print(f"Max workers  : {_max_workers}")

    run_log_path = settings.exps_dir_path / run_tag / "logs" / "run.log"
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(run_log_path, "a", encoding="utf-8") as run_log_file:
        state = RunState(
            run_tag=run_tag,
            total_tasks=len(task_ids),
            progress={},
            log_buffer=deque(maxlen=3),
            log_lock=threading.Lock(),
            log_file=run_log_file,
            pass1_start=time.monotonic(),
            pass1_rate_start=None,
            pass2_start=None,
        )

        global_log(
            state, f"=== run started  tasks={len(task_ids)} workers={_max_workers} ==="
        )

        # Pre-populate progress for ALL tasks so ETA covers the full workload,
        # not just the sessions owned by the currently-active workers.
        for tid in task_ids:
            n = get_session_count(dataset, tid)
            sess = n if max_sessions is None else min(n, max_sessions)
            state.progress[tid] = {
                "status": "pending",
                "phase": "summary",
                "done_summary": 0,
                "total_summary": sess,
                "summary_base": 0,
                "done_ingest": 0,
                "total_ingest": sess,
                "ingest_base": 0,
            }

        root = logging.getLogger()
        stream_handlers = [
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        for h in stream_handlers:
            root.removeHandler(h)

        stop_event = threading.Event()
        display_thread = threading.Thread(
            target=_display_rich_ui,
            args=(state, stop_event),
            daemon=True,
        )
        display_thread.start()

        results: list[dict] = []

        with ThreadPoolExecutor(max_workers=_max_workers) as executor:
            future_to_task = {
                executor.submit(
                    _worker_run_task,
                    dataset,
                    tid,
                    task_pks[tid],
                    max_sessions,
                    start_session,
                    resume,
                    run_tag,
                    state,
                    summary_only,
                    regen_summary,
                    regen_ingestion,
                ): tid
                for tid in task_ids
            }

            for future in as_completed(future_to_task):
                tid = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(
                        {"task_id": tid, "status": "failed", "error": str(e)}
                    )

        stop_event.set()
        display_thread.join(timeout=3)

    print(f"\n{'=' * 60}")
    print(f"Run '{run_tag}' Summary:")
    for r in results:
        icon = {"completed": "+", "failed": "!"}.get(r["status"], "?")
        print(f"  [{icon}] {r['task_id']}: {r['status']}")
        if r.get("error"):
            print(f"      {r['error'][:120]}")

    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        print(f"\n{len(failed)} task(s) failed.")
        return 1

    print("\nAll tasks completed successfully.")
    return 0
