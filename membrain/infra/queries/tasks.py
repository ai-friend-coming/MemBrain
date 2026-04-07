"""Database access helpers for pipeline routes.

This module keeps SQLAlchemy query details out of HTTP route handlers.
"""

from __future__ import annotations

from membrain.infra.db import SessionLocal
from membrain.infra.models.dataset import DatasetModel, TaskModel


def get_task_pk(dataset: str, task_id: str) -> int | None:
    """Resolve task primary key from dataset name + task_id."""
    with SessionLocal() as db:
        row = (
            db.query(TaskModel.id)
            .join(DatasetModel, TaskModel.dataset_id == DatasetModel.id)
            .filter(DatasetModel.name == dataset, TaskModel.task_id == task_id)
            .first()
        )
    return row[0] if row else None
