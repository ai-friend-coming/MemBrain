"""Entity-tree node DB query functions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from membrain.infra.models.memory import EntityModel


def batch_get_entities(
    db: Session,
    task_id: int,
    entity_ids: list[str],
) -> dict[str, EntityModel]:
    """Load EntityModels for multiple entity_ids in one query."""
    if not entity_ids:
        return {}
    rows = (
        db.query(EntityModel)
        .filter(EntityModel.task_id == task_id, EntityModel.entity_id.in_(entity_ids))
        .all()
    )
    return {e.entity_id: e for e in rows}
