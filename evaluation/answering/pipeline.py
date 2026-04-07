"""QA answering pipeline core logic.

The CLI entry point is ``evaluation.cli.exp_main`` (``uv run exp evaluate``).
This module exposes ``run_qa_pipeline()`` and helper functions for direct import.
"""

import json
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from evaluation.answering.judging.simple import judge_pair_with_retry  # noqa: E402
from evaluation.answering.profiles import get_profile  # noqa: E402
from evaluation.answering.profiles.base import BaseEvalProfile  # noqa: E402
from evaluation.models.qa import QAPairModel  # noqa: E402
from evaluation.runtime.local_search import LocalSearchRunner  # noqa: E402
from evaluation.ui.eval_console import _display_rich_eval_ui  # noqa: E402
from evaluation.utils.tasks import get_tasks_for_run, pipeline_log  # noqa: E402
from membrain.config import settings  # noqa: E402
from membrain.infra.db import SessionLocal, init_memory_db  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class EvalState:
    """Shared state between pipeline workers and the Rich display thread."""

    run_tag: str
    total: int
    # question_id → {status: "answering"|"judging"|"done"|"error", task_id: str}
    # Workers write their own slot only (CPython GIL safe for dict slot updates).
    progress: dict
    correct: int  # protected by progress_lock
    judged: int  # protected by progress_lock
    progress_lock: threading.Lock
    log_buffer: deque  # maxlen=5; error/retry events only
    log_lock: threading.Lock
    jsonl_lock: threading.Lock  # protects concurrent appends to JSONL file
    log_file: IO[str]  # open handle for exps/{run_tag}/logs/eval.log
    start_time: float  # time.monotonic()


eval_log = pipeline_log


def _append_jsonl(path: Path, entry: dict, lock: threading.Lock) -> None:
    """Atomically append one JSON line to a JSONL file."""
    line = json.dumps(entry, ensure_ascii=False)
    with lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _load_resume_state(run_tag: str, timestamp: str) -> tuple[Path, set[str], int, int]:
    """Read an existing JSONL log and extract resume state.

    Returns (jsonl_path, done_question_ids, init_judged, init_correct).
    Raises FileNotFoundError if the log doesn't exist, ValueError if corrupt.
    """
    jsonl_path = (
        settings.exps_dir_path / run_tag / "qa_logs" / f"eval_{timestamp}.jsonl"
    )
    if not jsonl_path.exists():
        raise FileNotFoundError(f"Resume log not found: {jsonl_path}")

    done_ids: set[str] = set()
    init_judged = 0
    init_correct = 0
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                done_ids.add(entry["question_id"])
                init_judged += 1
                if entry.get("judge_label") == "CORRECT":
                    init_correct += 1
    except json.JSONDecodeError as exc:
        raise ValueError(f"Resume log is corrupt ({jsonl_path}): {exc}") from exc

    return jsonl_path, done_ids, init_judged, init_correct


def load_qa_pairs(
    task_pk: int,
    category: str | None = None,
) -> list[QAPairModel]:
    with SessionLocal() as db:
        q = db.query(QAPairModel).filter(QAPairModel.task_id == task_pk)
        if category is not None:
            q = q.filter(QAPairModel.category == category)
        q = q.order_by(QAPairModel.id)
        pairs = q.all()
        db.expunge_all()
        return pairs


def _process_one(
    qa: QAPairModel,
    idx: int,
    total: int,
    task_pk: int,
    task_id: str,
    dataset_name: str,
    profile: BaseEvalProfile,
    client: LocalSearchRunner,
    http_client: httpx.Client,
    model: str,
    top_k: int,
    run_tag: str,
    state: EvalState,
    jsonl_path: Path,
    num_judge_runs: int,
    judge_model: str,
) -> None:
    """Answer one QA question then judge it inline; update state and write JSONL."""
    qid = qa.question_id
    state.progress[qid] = {"status": "answering", "task_id": task_id}

    try:
        # ── Answering stage ──────────────────────────────────────────────────
        context_text = profile.retrieve(client, task_pk, qa, top_k, run_tag)
        context_token_count = len(context_text) // 4 + 1

        for _attempt in range(10):
            try:
                predicted = profile.generate_answer(
                    qa, context_text, model, http_client
                )
                break
            except Exception as exc:
                if _attempt == 9:
                    raise
                eval_log(
                    state, f"Retry answering {qid} attempt {_attempt + 1}/10: {exc}"
                )

        # ── Judging stage ────────────────────────────────────────────────────
        state.progress[qid]["status"] = "judging"
        item = {
            "question_id": qid,
            "question": qa.question,
            "category": qa.category,
            "gold_answer": qa.answer,
            "predicted_answer": predicted,
        }
        if profile.use_exact_match:
            is_correct = predicted.strip().upper() == qa.answer.strip().upper()
            label = "CORRECT" if is_correct else "WRONG"
            judged = {
                "label": label,
                "judgments": {
                    f"judgment_{i + 1}": label for i in range(num_judge_runs)
                },
                "correct_votes": num_judge_runs if is_correct else 0,
                "num_runs": num_judge_runs,
            }
        else:
            judged = judge_pair_with_retry(
                item,
                judge_model,
                num_judge_runs,
                retry_interval=3.0,
                index=idx,
                total=total,
                http_client=http_client,
                error_log_fn=lambda msg: eval_log(state, msg),
            )

        # ── Update shared state ──────────────────────────────────────────────
        with state.progress_lock:
            state.judged += 1
            if judged["label"] == "CORRECT":
                state.correct += 1

        # ── Write JSONL ──────────────────────────────────────────────────────
        _append_jsonl(
            jsonl_path,
            {
                "question_id": qid,
                "task_id": task_id,
                "dataset": dataset_name,
                "category": qa.category,
                "question": qa.question,
                "gold_answer": qa.answer,
                "packed_context": context_text,
                "context_token_count": context_token_count,
                "predicted_answer": predicted,
                "judge_label": judged["label"],
                "judge_votes": judged["judgments"],
                "correct_votes": judged["correct_votes"],
                "num_runs": judged["num_runs"],
            },
            state.jsonl_lock,
        )

    except Exception:
        state.progress[qid]["status"] = "error"
        raise
    else:
        state.progress[qid]["status"] = "done"


def run_qa_pipeline(
    run_tag: str,
    top_k: int | None = None,
    model: str | None = None,
    category: str | None = None,
    workers: int = 5,
    resume: str | None = None,
    judge_model: str = "gpt-4.1-mini",
    num_judge_runs: int = 3,
    ranker: str = "rrf",
) -> int:
    """Execute the QA answering + judging pipeline (no CLI layer).

    Returns 0 on success, non-zero on failure.
    """
    top_k = top_k or settings.QA_RERANK_TOP_K
    model = model or settings.QA_LLM_MODEL

    init_memory_db()

    pairs = get_tasks_for_run(run_tag)
    if not pairs:
        print(f"Run '{run_tag}' not found or has no tasks.")
        return 1

    qa_pool: list[tuple[int, str, str, QAPairModel]] = []
    for task_pk, task_id, dataset_name in pairs:
        qa_pairs = load_qa_pairs(task_pk, category)
        for qa in qa_pairs:
            qa_pool.append((task_pk, task_id, dataset_name, qa))

    if not qa_pool:
        print("No QA pairs found across all tasks.")
        return 0

    total = len(qa_pool)

    # ── Resume: filter out already-done questions ─────────────────────────────
    init_judged = 0
    init_correct = 0
    if resume is not None:
        try:
            jsonl_path, done_ids, init_judged, init_correct = _load_resume_state(
                run_tag, resume
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}")
            return 1
        qa_pool = [
            (pk, tid, ds, qa)
            for pk, tid, ds, qa in qa_pool
            if qa.question_id not in done_ids
        ]
        if not qa_pool:
            print(f"All {total} questions already complete. Nothing to do.")
            return 0
        print(f"Resuming: {init_judged} already done, {len(qa_pool)} remaining.")
    else:
        # ── Set up new JSONL log ──────────────────────────────────────────────
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        jsonl_dir = settings.exps_dir_path / run_tag / "qa_logs"
        jsonl_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = jsonl_dir / f"eval_{timestamp}.jsonl"

    # ── Set up eval log file ─────────────────────────────────────────────────
    eval_log_path = settings.exps_dir_path / run_tag / "logs" / "eval.log"
    eval_log_path.parent.mkdir(parents=True, exist_ok=True)

    client = LocalSearchRunner(strategy=ranker)
    http_client = httpx.Client(timeout=30.0)

    # Build profile cache (one instance per dataset)
    _profile_cache: dict[str, BaseEvalProfile] = {}

    def _get_or_create_profile(ds: str) -> BaseEvalProfile:
        if ds not in _profile_cache:
            _profile_cache[ds] = get_profile(ds, ranker=ranker)
        return _profile_cache[ds]

    state: EvalState | None = None
    try:
        with open(eval_log_path, "a", encoding="utf-8") as log_file:
            state = EvalState(
                run_tag=run_tag,
                total=total,
                progress={},
                correct=init_correct,
                judged=init_judged,
                progress_lock=threading.Lock(),
                log_buffer=deque(maxlen=5),
                log_lock=threading.Lock(),
                jsonl_lock=threading.Lock(),
                log_file=log_file,
                start_time=time.monotonic(),
            )

            stop_event = threading.Event()
            display_thread = threading.Thread(
                target=_display_rich_eval_ui,
                args=(state, stop_event, workers),
                daemon=True,
            )
            display_thread.start()

            try:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _process_one,
                            qa,
                            i + 1,
                            total,
                            task_pk,
                            task_id,
                            dataset_name,
                            _get_or_create_profile(dataset_name),
                            client,
                            http_client,
                            model,
                            top_k,
                            run_tag,
                            state,
                            jsonl_path,
                            num_judge_runs,
                            judge_model,
                        ): qa.question_id
                        for i, (task_pk, task_id, dataset_name, qa) in enumerate(
                            qa_pool
                        )
                    }
                    for future in as_completed(futures):
                        qid = futures[future]
                        try:
                            future.result()
                        except Exception as exc:
                            eval_log(state, f"Error: {qid}: {exc}")
            finally:
                stop_event.set()
                display_thread.join()

    finally:
        client.cleanup()
        http_client.close()

    # ── Final summary (printed after UI stops) ───────────────────────────────
    if state is not None:
        judged = state.judged
        correct = state.correct
        accuracy = correct / judged if judged else 0.0
        json_path = _write_summary_json(jsonl_path)
        print(
            f"\nEval complete: {judged}/{total} judged  "
            f"accuracy={accuracy:.1%}  ({correct} correct)\n"
            f"JSONL log: {jsonl_path}\n"
            f"Summary JSON: {json_path}"
        )
    return 0


def _write_summary_json(jsonl_path: Path) -> Path:
    """Parse *jsonl_path* and write a human-readable summary JSON beside it.

    The output omits ``packed_context`` and mirrors the ``raw_ref/res.json``
    schema::

        {
          "total_questions": int,
          "correct": int,
          "accuracy": float,
          "detailed_results": {
            "<task_id>": [
              {
                "question_id": str,
                "question": str,
                "golden_answer": str | int,
                "generated_answer": str,
                "llm_judgments": {"judgment_1": bool, ...},
                "category": str
              }
            ]
          }
        }
    """
    entries: list[dict] = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    total = len(entries)
    correct = sum(1 for e in entries if e.get("judge_label") == "CORRECT")
    accuracy = correct / total if total else 0.0

    detailed: dict[str, list] = {}
    for e in entries:
        task_key = e.get("task_id", "unknown")
        detailed.setdefault(task_key, []).append(
            {
                "question_id": e["question_id"],
                "question": e["question"],
                "golden_answer": e["gold_answer"],
                "generated_answer": e["predicted_answer"],
                "correct": e.get("judge_label") == "CORRECT",
                "llm_judgments": {
                    k: v == "CORRECT" for k, v in e.get("judge_votes", {}).items()
                },
                "category": e.get("category"),
            }
        )

    summary = {
        "total_questions": total,
        "correct": correct,
        "accuracy": accuracy,
        "detailed_results": detailed,
    }

    json_path = jsonl_path.with_suffix(".json")
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Write a separate file containing only incorrect entries (with packed_context)
    wrong_detailed: dict[str, list] = {}
    for e in entries:
        if e.get("judge_label") == "CORRECT":
            continue
        task_key = e.get("task_id", "unknown")
        wrong_detailed.setdefault(task_key, []).append(
            {
                "question_id": e["question_id"],
                "question": e["question"],
                "golden_answer": e["gold_answer"],
                "generated_answer": e["predicted_answer"],
                "packed_context": e.get("packed_context", ""),
                "llm_judgments": {
                    k: v == "CORRECT" for k, v in e.get("judge_votes", {}).items()
                },
                "category": e.get("category"),
            }
        )

    wrong_path = jsonl_path.with_name(jsonl_path.stem + "_wrong.json")
    with wrong_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "total_wrong": total - correct,
                "total_questions": total,
                "accuracy": accuracy,
                "detailed_results": wrong_detailed,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    return json_path
