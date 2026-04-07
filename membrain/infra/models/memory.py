"""ORM models for memory pipeline tables."""

from enum import Enum

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from membrain.config import settings
from membrain.infra.db import Base


class FactStatus(str, Enum):
    """Status of an atomic fact record."""

    ACTIVE = "active"
    INVALIDATED = "invalidated"


class EntityModel(Base):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("task_id", "entity_id", name="uq_entity_task_eid"),
        Index("ix_entities_task_eid", "task_id", "entity_id"),
        {"info": {"per_task": True}},
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False)
    entity_id = Column(String(64), nullable=False)
    canonical_ref = Column(String(512), nullable=False)
    desc = Column(Text, nullable=False, default="")
    desc_embedding = Column(HALFVEC(settings.EMBED_DIM), nullable=True)
    batch_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class FactModel(Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_task_id", "task_id"),
        {"info": {"per_task": True}},
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    search_text = Column(Text, nullable=True)
    text_embedding = Column(HALFVEC(settings.EMBED_DIM), nullable=True)
    batch_id = Column(String(128), nullable=True)
    session_number = Column(Integer, nullable=True)
    batch_index = Column(Integer, nullable=True)
    fact_ts = Column(String(64), nullable=True)
    status = Column(
        String(20), nullable=False, default=FactStatus.ACTIVE, server_default="active"
    )
    created_at = Column(DateTime, server_default=func.now())


class FactRefModel(Base):
    __tablename__ = "fact_refs"
    __table_args__ = (
        UniqueConstraint(
            "fact_id", "entity_id", "alias_text", name="uq_factref_fact_ent_alias"
        ),
        {"info": {"per_task": True}},
    )

    id = Column(Integer, primary_key=True)
    fact_id = Column(
        Integer, ForeignKey("facts.id", ondelete="CASCADE"), nullable=False
    )
    entity_id = Column(String(64), nullable=False, index=True)
    alias_text = Column(String(512), nullable=False)

    fact = relationship("FactModel")


class TimeAnnotationModel(Base):
    __tablename__ = "time_annotations"
    __table_args__ = ({"info": {"per_task": True}},)

    id = Column(Integer, primary_key=True)
    fact_id = Column(
        Integer, ForeignKey("facts.id", ondelete="CASCADE"), nullable=False
    )
    time_raw = Column(Text, nullable=False)
    time_resolved = Column(String(64), nullable=False)

    fact = relationship("FactModel")


# ── Entity tree (hierarchical) ──


class EntityTreeNodeModel(Base):
    __tablename__ = "entity_tree_nodes"
    __table_args__ = (
        Index("ix_tree_task_entity", "task_id", "entity_id"),
        Index("ix_tree_parent", "parent_id"),
        Index("ix_tree_fact", "fact_id"),
        CheckConstraint(
            "node_type IN ('root', 'aspect', 'leaf')",
            name="ck_tree_node_type",
        ),
        {"info": {"per_task": True}},
        # Full unique constraint removed — the partial unique index
        # (uq_tree_leaf_fact_partial WHERE fact_id IS NOT NULL) in init_memory_db()
        # is the correct constraint for preventing duplicate leaf nodes.
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False)
    entity_id = Column(String(64), nullable=False)
    parent_id = Column(
        Integer,
        ForeignKey("entity_tree_nodes.id", ondelete="CASCADE"),
        nullable=True,
    )
    node_type = Column(String(20), nullable=False)  # 'root' | 'aspect' | 'leaf'
    fact_id = Column(Integer, ForeignKey("facts.id", ondelete="CASCADE"), nullable=True)
    description = Column(Text, nullable=True)
    description_embedding = Column(HALFVEC(settings.EMBED_DIM), nullable=True)
    priority_score = Column(Float, default=0.0, server_default="0")
    uncertainty_score = Column(Float, default=0.0, server_default="0")
    support = Column(Integer, default=0, server_default="0")
    fresh_count = Column(Integer, default=0, server_default="0")
    subtree_centroid = Column(HALFVEC(settings.EMBED_DIM), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    parent = relationship(
        "EntityTreeNodeModel",
        remote_side="EntityTreeNodeModel.id",
        backref="children_rel",
    )
    fact = relationship("FactModel")


# ── Pipeline tracing models ──


class SessionSummaryModel(Base):
    """Per-session narrative summary for episode-level retrieval."""

    __tablename__ = "session_summaries"
    __table_args__ = (
        UniqueConstraint("task_id", "session_id", name="uq_session_summary"),
        {"info": {"per_task": True}},
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False, index=True)
    session_id = Column(
        Integer,
        ForeignKey("chat_sessions.id"),
        nullable=False,
        index=True,
    )
    subject = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
