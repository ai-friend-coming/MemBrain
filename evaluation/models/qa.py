"""Evaluation-specific QA ORM model."""

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from membrain.infra.db import Base


class QAPairModel(Base):
    __tablename__ = "qa_pairs"
    __table_args__ = (
        UniqueConstraint("task_id", "question_id", name="uq_qa_task_question"),
        Index("ix_qa_pairs_category", "category"),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    question_id = Column(String(255), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    category = Column(String(50))
    evidence = Column(Text)
    options = Column(Text, nullable=True)
    reasoning = Column(Text, nullable=True)

    task = relationship("TaskModel")
