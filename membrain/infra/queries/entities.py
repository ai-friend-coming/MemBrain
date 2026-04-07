"""Entity-related DB query functions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from membrain.infra.models.memory import EntityModel, FactRefModel


def build_ref_map(db: Session, task_id: int) -> dict[str, str]:
    """Return mapping of canonical_ref/alias_text -> entity_id for a task.

    Combines EntityModel.canonical_ref rows and FactRefModel.alias_text rows.
    FactRef aliases do not overwrite canonical refs.
    """
    entity_rows = (
        db.query(EntityModel.canonical_ref, EntityModel.entity_id)
        .filter(EntityModel.task_id == task_id)
        .all()
    )
    ref_map: dict[str, str] = {r[0]: r[1] for r in entity_rows}

    fr_rows = db.query(FactRefModel.alias_text, FactRefModel.entity_id).distinct().all()
    for alias_text, entity_id in fr_rows:
        ref_map.setdefault(alias_text, entity_id)

    return ref_map


def find_merge_targets(
    db: Session, task_id: int, entity_ids: list[str]
) -> dict[str, EntityModel]:
    """Bulk-load EntityModel objects by entity_id list.

    Returns dict keyed by entity_id. Missing ids are absent from the result.
    """
    if not entity_ids:
        return {}
    rows = (
        db.query(EntityModel)
        .filter(
            EntityModel.task_id == task_id,
            EntityModel.entity_id.in_(entity_ids),
        )
        .all()
    )
    return {e.entity_id: e for e in rows}
