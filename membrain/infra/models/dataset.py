"""ORM models for core dataset tables: datasets, tasks, sessions, messages."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from membrain.infra.db import Base


class DatasetModel(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)

    tasks = relationship(
        "TaskModel", back_populates="dataset", cascade="all, delete-orphan"
    )


class TaskModel(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("dataset_id", "task_id", name="uq_task_dataset_taskid"),
    )

    id = Column(Integer, primary_key=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=False)
    task_id = Column(String(255), nullable=False)
    agent_profile = Column(String(64), nullable=True)

    dataset = relationship("DatasetModel", back_populates="tasks")
    sessions = relationship(
        "ChatSessionModel",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ChatSessionModel.session_number",
    )


class ChatSessionModel(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("task_id", "session_number", name="uq_session_task_number"),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    session_number = Column(Integer, nullable=False)
    session_time = Column(DateTime, nullable=True)
    session_time_raw = Column(String(255))
    digested_at = Column(DateTime, nullable=True)

    task = relationship("TaskModel", back_populates="sessions")
    messages = relationship(
        "ChatMessageModel",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageModel.position",
    )


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_message_session_pos"),
    )

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    position = Column(Integer, nullable=False)
    speaker = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    message_time = Column(DateTime, nullable=True)
    message_time_raw = Column(String(255))

    session = relationship("ChatSessionModel", back_populates="messages")
