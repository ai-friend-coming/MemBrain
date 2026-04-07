"""Checkpoint management: save and restore run state using pg_dump / pg_restore."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from membrain.config import settings
from membrain.infra.db import SessionLocal
from membrain.infra.models.dataset import TaskModel

log = logging.getLogger(__name__)


# ── Paths ────────────────────────────────────────────────────────────────────


def _task_id_from_pk(task_pk: int) -> str:
    with SessionLocal() as db:
        row = db.query(TaskModel.task_id).filter(TaskModel.id == task_pk).first()
        return row[0] if row else str(task_pk)


def _ckpt_dir(task_pk: int, run_tag: str) -> Path:
    return settings.exps_dir_path / run_tag / _task_id_from_pk(task_pk) / "ckpts"


def _dump_path(task_pk: int, run_tag: str) -> Path:
    return _ckpt_dir(task_pk, run_tag) / "checkpoint.dump"


def _meta_path(task_pk: int, run_tag: str) -> Path:
    return _ckpt_dir(task_pk, run_tag) / "checkpoint.dump.meta.json"


def _plain_url() -> str:
    """Strip SQLAlchemy driver prefix for pg_dump / psql."""
    return settings.DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")


def _schema_name(task_pk: int, run_tag: str) -> str:
    return f"task_{task_pk}__{run_tag}"


_PG_TOOLS = {"pg_dump", "pg_restore", "psql"}
_PG_CONTAINER = "MemBrain"
_CONTAINER_EXPS = "/exps"

# Serialize concurrent pg_restore calls: ParadeDB's BM25 shared catalog
# deadlocks when multiple `CREATE INDEX USING bm25` run simultaneously
# (as happens when concurrent workers all restore from checkpoint on resume).
# pg_restore subprocesses don't participate in our pg_advisory_xact_lock,
# so we guard them with a process-level threading lock instead.
_restore_lock = threading.Lock()


def _wrap_cmd(cmd: list[str]) -> list[str]:
    """Run pg_* tools inside the DB container to avoid client/server version mismatch."""
    if cmd and cmd[0] in _PG_TOOLS:
        return ["docker", "exec", _PG_CONTAINER] + cmd
    return cmd


def _to_container_path(host_path: Path) -> str:
    """Convert a host path under EXPS_DIR to the container-mounted equivalent."""
    exps_root = settings.exps_dir_path
    host_path = host_path.resolve()
    rel = host_path.relative_to(exps_root)
    return f"{_CONTAINER_EXPS}/{rel}"


def _run(cmd: list[str], label: str) -> None:
    result = subprocess.run(_wrap_cmd(cmd), capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed:\n{result.stderr}")


def _run_raw(cmd: list[str], label: str) -> None:
    """Run a command directly (no _wrap_cmd)."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"{label} failed:\n{result.stderr}")


# ── Public API ────────────────────────────────────────────────────────────────


def save_checkpoint(
    task_pk: int,
    run_tag: str,
    batch_index: int,
    session_number: int,
    batch_within_session: int,
    batch_id: str,
    pass_number: int,
) -> None:
    """pg_dump the per-run schema to disk (atomic write, keep only latest)."""
    schema = _schema_name(task_pk, run_tag)
    ckpt_dir = _ckpt_dir(task_pk, run_tag)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    dump_path = ckpt_dir / "checkpoint.dump"
    meta_path = ckpt_dir / "checkpoint.dump.meta.json"
    tmp_dump = dump_path.with_suffix(".tmp")
    tmp_meta = meta_path.with_suffix(".tmp")

    try:
        _run(
            [
                "pg_dump",
                "-n",
                schema,
                "-F",
                "c",
                "-f",
                _to_container_path(tmp_dump),
                _plain_url(),
            ],
            "pg_dump",
        )
        meta = {
            "pass": pass_number,
            "batch_index": batch_index,
            "session_number": session_number,
            "batch_within_session": batch_within_session,
            "batch_id": batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_meta.write_text(json.dumps(meta, indent=2))
        tmp_dump.replace(dump_path)
        tmp_meta.replace(meta_path)
    except Exception:
        tmp_dump.unlink(missing_ok=True)
        tmp_meta.unlink(missing_ok=True)
        raise

    log.info("Checkpoint saved: schema=%s batch_index=%d", schema, batch_index)


def load_checkpoint_meta(task_pk: int, run_tag: str) -> dict | None:
    """Read checkpoint sidecar JSON, or return None if absent."""
    path = _meta_path(task_pk, run_tag)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _drop_schema_with_retry(schema: str, max_retries: int = 5) -> None:
    """DROP SCHEMA CASCADE with deadlock retry."""
    import time

    cmd = ["psql", _plain_url(), "-c", f"DROP SCHEMA IF EXISTS {schema} CASCADE"]
    for attempt in range(max_retries):
        try:
            _run(cmd, "DROP SCHEMA")
            return
        except RuntimeError as e:
            if "deadlock" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(1 + attempt)
                log.warning(
                    "DROP SCHEMA deadlock, retry %d/%d", attempt + 1, max_retries - 1
                )
                continue
            raise


def restore_checkpoint(task_pk: int, run_tag: str) -> None:
    """Drop the per-run schema and restore it from the pg_dump file."""
    schema = _schema_name(task_pk, run_tag)
    dump = _dump_path(task_pk, run_tag)
    if not dump.exists():
        raise FileNotFoundError(f"No checkpoint dump at {dump}")

    with _restore_lock:
        _drop_schema_with_retry(schema)
        url = _plain_url()
        _run(
            ["pg_restore", "-F", "c", "-d", url, _to_container_path(dump)], "pg_restore"
        )
    log.info("Checkpoint restored: schema=%s", schema)


def delete_checkpoint(task_pk: int, run_tag: str) -> None:
    """Remove dump file and sidecar."""
    ckpt_dir = _ckpt_dir(task_pk, run_tag)
    (ckpt_dir / "checkpoint.dump").unlink(missing_ok=True)
    (ckpt_dir / "checkpoint.dump.meta.json").unlink(missing_ok=True)
    log.info("Checkpoint deleted: task_pk=%d run_tag=%s", task_pk, run_tag)


def _done_path(task_pk: int, run_tag: str) -> Path:
    return _ckpt_dir(task_pk, run_tag) / "done"


def mark_done(task_pk: int, run_tag: str) -> None:
    """Write a 'done' marker file indicating all sessions are complete."""
    p = _done_path(task_pk, run_tag)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    log.info("Marked done: task_pk=%d run_tag=%s", task_pk, run_tag)


_RUN_RESERVED_DIRS = frozenset({"logs", "qa_logs"})


def list_run_tasks(run_tag: str) -> list[str]:
    """Return task_id strings found under exps/{run_tag}/."""
    run_dir = settings.exps_dir_path / run_tag
    if not run_dir.is_dir():
        return []
    return [
        d.name
        for d in run_dir.iterdir()
        if d.is_dir() and d.name not in _RUN_RESERVED_DIRS
    ]


def save_run_meta(run_tag: str, dataset: str) -> None:
    """Persist run-level metadata (dataset name) to exps/{run_tag}/run.meta.json."""
    run_dir = settings.exps_dir_path / run_tag
    run_dir.mkdir(parents=True, exist_ok=True)
    meta_file = run_dir / "run.meta.json"
    meta_file.write_text(json.dumps({"dataset": dataset}, indent=2))


def load_run_meta(run_tag: str) -> dict:
    """Load run-level metadata from exps/{run_tag}/run.meta.json."""
    meta_file = settings.exps_dir_path / run_tag / "run.meta.json"
    return json.loads(meta_file.read_text())


def is_task_done_by_id(run_tag: str, task_id: str) -> bool:
    """Check done marker using string task_id (no DB lookup needed)."""
    return (settings.exps_dir_path / run_tag / task_id / "ckpts" / "done").exists()


def clear_ingestion_tables(task_pk: int, run_tag: str) -> None:
    """Truncate all Pass-2 tables, preserving session_summaries."""
    schema = _schema_name(task_pk, run_tag)
    tables = ", ".join(
        f"{schema}.{t}"
        for t in [
            "entity_tree_nodes",
            "fact_refs",
            "time_annotations",
            "facts",
            "entities",
        ]
    )
    cmd = ["psql", _plain_url(), "-c", f"TRUNCATE {tables} CASCADE"]
    _run(cmd, "TRUNCATE ingestion tables")
    log.info("Ingestion tables cleared: schema=%s", schema)


def purge_task_memory(task_pk: int, run_tag: str) -> None:
    """DROP SCHEMA CASCADE and remove task experiment directory."""
    schema = _schema_name(task_pk, run_tag)
    _drop_schema_with_retry(schema)
    # Delete via docker exec so deletion is confined to the /exps mount
    task_dir = _ckpt_dir(task_pk, run_tag).parent  # .../run_tag/task_id/
    container_path = _to_container_path(task_dir)
    _run_raw(
        ["docker", "exec", _PG_CONTAINER, "rm", "-rf", container_path], "rm task dir"
    )
    log.info("Purged task memory: schema=%s dir=%s", schema, container_path)


def copy_run(old_run_tag: str, new_run_tag: str) -> tuple[int, list[str]]:
    """Copy a run to a new run tag within the same database.

    For each task:
      1. init_run_schema -> creates new schema with tables + indexes
      2. INSERT INTO new_schema.table SELECT * FROM old_schema.table (each table)
      3. Reset sequences
      4. pg_dump new_schema -> checkpoint.dump
      5. Write checkpoint meta, copy done marker

    Returns (success_count, error_list).
    Raises ValueError if old_run_tag not found or new_run_tag already exists.
    """
    from sqlalchemy import text as sql_text

    run_dir = settings.exps_dir_path / old_run_tag
    if not run_dir.is_dir():
        raise ValueError(f"Run '{old_run_tag}' not found in {settings.exps_dir_path}")
    if (settings.exps_dir_path / new_run_tag).exists():
        raise ValueError(f"Run tag '{new_run_tag}' already exists")

    meta = load_run_meta(old_run_tag)
    dataset = meta["dataset"]
    task_ids = list_run_tasks(old_run_tag)

    from membrain.infra.db import create_run_engine, init_run_schema
    from membrain.infra.models.dataset import DatasetModel

    errors: list[str] = []
    successes = 0

    from tqdm import tqdm

    for task_id in tqdm(task_ids, desc=f"Copying {old_run_tag} → {new_run_tag}"):
        try:
            with SessionLocal() as db:
                row = (
                    db.query(TaskModel.id)
                    .join(DatasetModel)
                    .filter(DatasetModel.name == dataset, TaskModel.task_id == task_id)
                    .first()
                )
            if row is None:
                errors.append(f"{task_id}: task not found in DB (dataset={dataset})")
                continue
            task_pk = row[0]

            old_schema = _schema_name(task_pk, old_run_tag)
            new_schema = _schema_name(task_pk, new_run_tag)

            # 1. Create new schema with tables + indexes
            eng = create_run_engine(task_pk, new_run_tag)
            try:
                init_run_schema(eng, task_pk, new_run_tag)

                # 2. Copy data (FK dependency order)
                with eng.connect() as conn:
                    for table in (
                        "session_summaries",
                        "entities",
                        "facts",
                        "fact_refs",
                        "time_annotations",
                    ):
                        conn.execute(
                            sql_text(
                                f"INSERT INTO {new_schema}.{table} "
                                f"SELECT * FROM {old_schema}.{table}"
                            )
                        )
                    # entity_tree_nodes: 3 passes to satisfy self-referential FK
                    for node_type in ("root", "aspect", "leaf"):
                        conn.execute(
                            sql_text(
                                f"INSERT INTO {new_schema}.entity_tree_nodes "
                                f"SELECT * FROM {old_schema}.entity_tree_nodes "
                                f"WHERE node_type = :nt"
                            ),
                            {"nt": node_type},
                        )
                    # 3. Reset sequences
                    for table in (
                        "session_summaries",
                        "entities",
                        "facts",
                        "fact_refs",
                        "time_annotations",
                        "entity_tree_nodes",
                    ):
                        conn.execute(
                            sql_text(
                                f"SELECT setval("
                                f"pg_get_serial_sequence('{new_schema}.{table}', 'id'), "
                                f"COALESCE((SELECT MAX(id) FROM {new_schema}.{table}), 0) + 1,"
                                f" false)"
                            )
                        )
                    conn.commit()
            finally:
                eng.dispose()

            # 4. pg_dump new schema -> checkpoint.dump
            ckpt_dir = settings.exps_dir_path / new_run_tag / task_id / "ckpts"
            ckpt_dir.mkdir(parents=True, exist_ok=True)
            dump_path = ckpt_dir / "checkpoint.dump"
            _run(
                [
                    "pg_dump",
                    "-n",
                    new_schema,
                    "-F",
                    "c",
                    "-f",
                    _to_container_path(dump_path),
                    _plain_url(),
                ],
                f"pg_dump {task_id}",
            )

            # 5. Write checkpoint meta
            with SessionLocal() as db:
                row2 = db.execute(
                    sql_text(
                        f"SELECT MAX(session_number), MAX(batch_index)"
                        f" FROM {new_schema}.facts"
                    )
                ).first()
            max_session = row2[0] or 0
            max_batch = row2[1] or 0

            (ckpt_dir / "checkpoint.dump.meta.json").write_text(
                json.dumps(
                    {
                        "pass": 2,
                        "session_number": max_session,
                        "batch_index": max_batch,
                        "batch_within_session": 0,
                        "batch_id": "",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                )
            )

            # 6. Copy done marker
            if is_task_done_by_id(old_run_tag, task_id):
                (ckpt_dir / "done").touch()

            successes += 1
            log.info("Copied task '%s': %s -> %s", task_id, old_schema, new_schema)
        except Exception as exc:
            errors.append(f"{task_id}: {exc}")
            log.error("Failed to copy task '%s': %s", task_id, exc)

    save_run_meta(new_run_tag, dataset)
    return successes, errors
