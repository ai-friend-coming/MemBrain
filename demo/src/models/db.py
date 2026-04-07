"""SQLAlchemy ORM models for sessions and messages."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from uuid_utils import uuid7


class Base(DeclarativeBase):
    pass


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    persona_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New Chat")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    messages: Mapped[list["MessageModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MessageModel.created_at",
    )

    __table_args__ = (Index("ix_sessions_persona_updated", "persona_id", "updated_at"),)


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    session: Mapped["SessionModel"] = relationship(back_populates="messages")

    __table_args__ = (Index("ix_messages_session_created_at", "session_id", "created_at"),)


class PersonaModel(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid7()))
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    character_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    character_biography: Mapped[str] = mapped_column(Text, nullable=False, default="")
    neta_uuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_img: Mapped[str | None] = mapped_column(Text, nullable=True)
    header_img: Mapped[str | None] = mapped_column(Text, nullable=True)
    character_reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    reflection_cursor_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    membrain_cursor_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    llm_api_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
