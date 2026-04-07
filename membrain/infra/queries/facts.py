"""Fact and fact-reference DB query functions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from membrain.infra.models.memory import (
    EntityTreeNodeModel,
    FactModel,
    FactRefModel,
    FactStatus,
)


def get_touched_entity_ids(db: Session, task_id: int, batch_id: str) -> list[str]:
    """Return entity_ids that had new facts written in this batch."""
    fact_ids = (
        db.query(FactModel.id)
        .filter(FactModel.task_id == task_id, FactModel.batch_id == batch_id)
        .subquery()
    )
    rows = (
        db.query(FactRefModel.entity_id)
        .filter(FactRefModel.fact_id.in_(db.query(fact_ids.c.id)))
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def load_facts(db: Session, fact_ids: list[int]) -> list[FactModel]:
    """Load FactModel objects by PK list, preserving order."""
    if not fact_ids:
        return []
    facts = db.query(FactModel).filter(FactModel.id.in_(fact_ids)).all()
    by_id = {f.id: f for f in facts}
    return [by_id[fid] for fid in fact_ids if fid in by_id]


def batch_find_new_facts_not_in_tree(
    db: Session,
    task_id: int,
    entity_ids: list[str],
) -> dict[str, list[int]]:
    """Batch anti-join: {entity_id: [new_fact_pks]} for all entities in one query."""
    if not entity_ids:
        return {}
    tree_hit = (
        db.query(EntityTreeNodeModel.id)
        .filter(
            EntityTreeNodeModel.task_id == task_id,
            EntityTreeNodeModel.entity_id == FactRefModel.entity_id,
            EntityTreeNodeModel.fact_id == FactModel.id,
        )
        .correlate(FactModel, FactRefModel)
        .exists()
    )
    rows = (
        db.query(FactRefModel.entity_id, FactModel.id)
        .join(FactRefModel, FactRefModel.fact_id == FactModel.id)
        .filter(
            FactModel.task_id == task_id,
            FactRefModel.entity_id.in_(entity_ids),
            FactModel.status == FactStatus.ACTIVE,
            ~tree_hit,
        )
        .distinct()
        .order_by(FactRefModel.entity_id, FactModel.id)
        .all()
    )
    result: dict[str, list[int]] = {eid: [] for eid in entity_ids}
    for entity_id, fact_id in rows:
        result[entity_id].append(fact_id)
    return result
