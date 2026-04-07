"""Shared utilities for pipeline scripts."""

from datetime import datetime

from membrain.infra.db import SessionLocal
from membrain.infra.models.dataset import DatasetModel, TaskModel


def pipeline_log(state, msg: str) -> None:
    """Append a message to the in-memory log buffer and the log file.

    Works with any state object that has .log_buffer, .log_lock, .log_file.
    Safe to call from any worker thread.
    """
    now = datetime.now()
    ts_short = now.strftime("%H:%M:%S")
    line = f"{ts_short}  {msg}"
    ts_iso = now.isoformat(timespec="seconds")
    with state.log_lock:
        state.log_buffer.append(line)
        state.log_file.write(f"{ts_iso}  {msg}\n")
        state.log_file.flush()


def resolve_task_spec(spec: str, dataset: str) -> list[tuple[int, str]]:
    """Parse a task specification into (pk, task_id_string) pairs.

    Numbers are 1-based indexes within the dataset (ordered by PK).
    Supports individual indexes and inclusive ranges: "1", "1,3,5", "1-5,8".
    """
    indexes: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            indexes.extend(range(int(start), int(end) + 1))
        else:
            indexes.append(int(part))

    with SessionLocal() as db:
        all_tasks = (
            db.query(TaskModel.id, TaskModel.task_id)
            .join(DatasetModel)
            .filter(DatasetModel.name == dataset)
            .order_by(TaskModel.id)
            .all()
        )

    if not all_tasks:
        raise ValueError(f"No tasks found in dataset '{dataset}'")

    result: list[tuple[int, str]] = []
    for idx in indexes:
        if idx < 1 or idx > len(all_tasks):
            raise ValueError(
                f"Task index {idx} out of range (dataset '{dataset}' has {len(all_tasks)} tasks)"
            )
        result.append(all_tasks[idx - 1])

    return result


def get_tasks_for_run(run_tag: str) -> list[tuple[int, str, str]]:
    """Return (pk, task_id, dataset_name) for all tasks recorded in a run."""
    from membrain.infra.checkpoint import list_run_tasks, load_run_meta

    task_ids = list_run_tasks(run_tag)
    if not task_ids:
        return []
    dataset_name = load_run_meta(run_tag)["dataset"]
    with SessionLocal() as db:
        rows = (
            db.query(TaskModel.id, TaskModel.task_id, DatasetModel.name)
            .join(DatasetModel)
            .filter(TaskModel.task_id.in_(task_ids))
            .filter(DatasetModel.name == dataset_name)
            .order_by(TaskModel.id)
            .all()
        )
        return list(rows)
