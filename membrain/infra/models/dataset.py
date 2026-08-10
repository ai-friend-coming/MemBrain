"""ORM models for core dataset tables: datasets, tasks, sessions, messages."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
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
    """保存一次内部会话及其外部聊天来源。"""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("task_id", "session_number", name="uq_session_task_number"),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    chat_id = Column(String(255), nullable=False, index=True)
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


class MemoryDigestJobModel(Base):
    """持久化一次可幂等恢复的异步记忆构建任务。"""

    __tablename__ = "memory_digest_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="memory_digest_jobs_status_chk",
        ),
    )

    request_id = Column(String(255), primary_key=True)
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    session_id = Column(
        Integer,
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status = Column(String(32), nullable=False, default="queued")
    digested_sessions = Column(Integer, nullable=False, default=0)
    trace = Column(JSON, nullable=False, default=dict)
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
