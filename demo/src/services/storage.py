"""Session and message storage backed by SQLite via SQLAlchemy."""

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import db_session, new_id
from ..models import SessionMetadata
from ..models.db import MessageModel, PersonaModel, SessionModel
from ..models.schemas import PersonaCreate, PersonaLLMUpdate

logger = logging.getLogger(__name__)

_DEFAULT_TITLE = "New Chat"


async def create_session(db: AsyncSession, persona_id: str) -> str:
    """Create a new empty session and return its ID. Commits internally."""
    session_id = new_id()
    now = datetime.now(UTC)
    db.add(
        SessionModel(
            id=session_id,
            persona_id=persona_id,
            title=_DEFAULT_TITLE,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return session_id


async def ensure_session(db: AsyncSession, persona_id: str, session_id: str) -> None:
    """Create session if it doesn't exist yet. Does NOT commit (caller manages transaction)."""
    result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
    if not result.scalar_one_or_none():
        now = datetime.now(UTC)
        db.add(
            SessionModel(
                id=session_id,
                persona_id=persona_id,
                title=_DEFAULT_TITLE,
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()


async def add_messages(
    db: AsyncSession, persona_id: str, session_id: str, messages: list[dict]
) -> None:
    """Insert new messages into a session. Does NOT commit (caller manages transaction).

    Each message dict must include: id, role, content, created_at (datetime).
    Auto-titles from first user message if session title is still the default.
    Messages are saved without embeddings.
    """
    if not messages:
        return

    for msg in messages:
        db.add(
            MessageModel(
                id=msg["id"],
                session_id=session_id,
                role=msg["role"],
                content=msg["content"],
                created_at=msg["created_at"],
            )
        )

    # Update session timestamp
    session_row = await db.get(SessionModel, session_id)
    if session_row:
        session_row.updated_at = datetime.now(UTC)

        # Auto-title from first user message if still default
        if session_row.title == _DEFAULT_TITLE:
            for msg in messages:
                if msg.get("role") == "user":
                    text = msg.get("content", "")
                    if text:
                        session_row.title = text[:50] + ("..." if len(text) > 50 else "")
                    break

    await db.flush()


async def list_sessions(db: AsyncSession, persona_id: str) -> list[dict]:
    """Return SessionMetadata list sorted by updated_at desc."""
    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.persona_id == persona_id)
        .order_by(SessionModel.updated_at.desc())
    )
    sessions = result.scalars().all()
    if not sessions:
        return []

    session_ids = [s.id for s in sessions]

    # Bulk fetch message counts
    count_rows = await db.execute(
        select(MessageModel.session_id, func.count().label("cnt"))
        .where(MessageModel.session_id.in_(session_ids))
        .group_by(MessageModel.session_id)
    )
    counts = {row.session_id: row.cnt for row in count_rows}

    # Bulk fetch last message per session via row_number window function
    rn = (
        func.row_number()
        .over(
            partition_by=MessageModel.session_id,
            order_by=MessageModel.created_at.desc(),
        )
        .label("rn")
    )
    ranked = (
        select(MessageModel.session_id, MessageModel.content, rn)
        .where(MessageModel.session_id.in_(session_ids))
        .subquery()
    )
    preview_rows = await db.execute(
        select(ranked.c.session_id, ranked.c.content).where(ranked.c.rn == 1)
    )
    previews = {row.session_id: row.content[:80] for row in preview_rows}

    return [
        SessionMetadata(
            id=s.id,
            title=s.title,
            createdAt=s.created_at.isoformat(),
            updatedAt=s.updated_at.isoformat(),
            messageCount=counts.get(s.id, 0),
            preview=previews.get(s.id, ""),
        ).model_dump()
        for s in sessions
    ]


async def get_session_detail(db: AsyncSession, persona_id: str, session_id: str) -> dict | None:
    """Return full session with messages in UI format."""
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.persona_id == persona_id,
        )
    )
    s = result.scalar_one_or_none()
    if not s:
        return None

    msg_result = await db.execute(
        select(MessageModel)
        .where(MessageModel.session_id == session_id)
        .order_by(MessageModel.created_at)
    )
    ui_messages = [
        {
            "id": m.id,
            "role": m.role,
            "parts": [{"type": "text", "text": m.content}],
        }
        for m in msg_result.scalars().all()
    ]

    return {
        "id": s.id,
        "title": s.title,
        "messages": ui_messages,
    }


async def delete_session(db: AsyncSession, persona_id: str, session_id: str) -> bool:
    """Delete a session. Commits internally."""
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.persona_id == persona_id,
        )
    )
    session_row = result.scalar_one_or_none()
    if not session_row:
        return False

    await db.delete(session_row)
    await db.commit()
    return True


async def get_recent_messages(session_id: str, limit: int) -> list[dict]:
    """Fetch the most recent `limit` messages for a session, ordered oldest-first.

    Returns list of {role, content} dicts suitable for history construction.
    """
    async with db_session() as db:
        result = await db.execute(
            select(MessageModel)
            .where(MessageModel.session_id == session_id)
            .order_by(MessageModel.created_at.desc())
            .limit(limit)
        )
        msgs = result.scalars().all()
        return [{"role": m.role, "content": m.content} for m in reversed(msgs)]


async def count_session_messages(session_id: str) -> int:
    """Count total messages in a session (for trunk-based window computation)."""
    async with db_session() as db:
        result = await db.execute(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.session_id == session_id)
        )
        return result.scalar() or 0


async def delete_messages_from(session_id: str, message_id: str) -> bool:
    """Delete a message and all messages after it (by created_at) in a session.

    Returns True if the target message was found. Commits internally.
    """
    async with db_session() as db:
        # Find the target message's created_at
        target = await db.get(MessageModel, message_id)
        if not target or target.session_id != session_id:
            return False

        await db.execute(
            delete(MessageModel).where(
                MessageModel.session_id == session_id,
                MessageModel.created_at >= target.created_at,
            )
        )
        await db.commit()
        return True


async def delete_persona_data(db: AsyncSession, persona_id: str) -> int:
    """Delete all sessions (and their messages via cascade) for a persona.
    Returns number of sessions deleted. Commits internally."""
    count_result = await db.execute(
        select(func.count()).select_from(SessionModel).where(SessionModel.persona_id == persona_id)
    )
    count = count_result.scalar() or 0
    await db.execute(delete(SessionModel).where(SessionModel.persona_id == persona_id))
    await db.commit()
    return count


async def update_title(db: AsyncSession, persona_id: str, session_id: str, title: str) -> bool:
    """Update session title. Returns True if session existed. Commits internally."""
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.id == session_id,
            SessionModel.persona_id == persona_id,
        )
    )
    session_row = result.scalar_one_or_none()
    if not session_row:
        return False
    session_row.title = title
    session_row.updated_at = datetime.now(UTC)
    await db.commit()
    return True


async def list_personas(db: AsyncSession, owner_id: str) -> list[PersonaModel]:
    """Return all personas owned by owner_id."""
    result = await db.execute(
        select(PersonaModel)
        .where(PersonaModel.owner_id == owner_id)
        .order_by(PersonaModel.created_at)
    )
    return list(result.scalars().all())


async def create_persona(db: AsyncSession, owner_id: str, data: PersonaCreate) -> PersonaModel:
    """Create and persist a new persona. Commits internally."""
    persona = PersonaModel(
        id=new_id(),
        owner_id=owner_id,
        user_alias=data.user_alias,
        character_name=data.character_name,
        character_biography=data.character_biography,
        neta_uuid=data.neta_uuid,
        avatar_img=data.avatar_img,
        header_img=data.header_img,
        llm_api_url=data.llm_api_url,
        llm_api_key=data.llm_api_key,
        created_at=datetime.now(UTC),
    )
    db.add(persona)
    await db.commit()
    await db.refresh(persona)
    return persona


async def get_persona(db: AsyncSession, persona_id: str) -> PersonaModel | None:
    """Return a persona by ID."""
    return await db.get(PersonaModel, persona_id)


async def update_persona_llm(
    db: AsyncSession, persona_id: str, owner_id: str, data: PersonaLLMUpdate
) -> PersonaModel | None:
    """Update LLM API settings for a persona. Commits internally."""
    result = await db.execute(
        select(PersonaModel).where(
            PersonaModel.id == persona_id,
            PersonaModel.owner_id == owner_id,
        )
    )
    persona = result.scalar_one_or_none()
    if not persona:
        return None
    persona.llm_api_url = data.llm_api_url or None
    persona.llm_api_key = data.llm_api_key or None
    await db.commit()
    await db.refresh(persona)
    return persona


async def delete_persona(db: AsyncSession, persona_id: str, owner_id: str) -> bool:
    """Delete persona record and all its data. Commits internally."""
    result = await db.execute(
        select(PersonaModel).where(
            PersonaModel.id == persona_id,
            PersonaModel.owner_id == owner_id,
        )
    )
    persona = result.scalar_one_or_none()
    if not persona:
        return False

    await delete_persona_data(db, persona_id)
    await db.delete(persona)
    await db.commit()
    return True


async def count_user_turns_in_session(session_id: str) -> int:
    """Count user messages in a session (= number of turns)."""
    async with db_session() as db:
        result = await db.execute(
            select(func.count()).where(
                MessageModel.session_id == session_id, MessageModel.role == "user"
            )
        )
        return result.scalar() or 0


async def count_user_turns_since(persona_id: str, since_at: datetime | None) -> int:
    """Count user turns across all persona sessions after since_at (None = all time)."""
    async with db_session() as db:
        q = (
            select(func.count())
            .select_from(MessageModel)
            .join(SessionModel, MessageModel.session_id == SessionModel.id)
            .where(
                SessionModel.persona_id == persona_id,
                MessageModel.role == "user",
            )
        )
        if since_at is not None:
            q = q.where(MessageModel.created_at > since_at)
        result = await db.execute(q)
        return result.scalar() or 0


async def get_messages_since(
    persona_id: str, since_at: datetime | None, lookback: int = 0
) -> list[dict]:
    """Messages across persona's sessions after since_at, plus up to `lookback` messages before cursor as context."""
    async with db_session() as db:
        base = (
            select(MessageModel)
            .join(SessionModel, MessageModel.session_id == SessionModel.id)
            .where(SessionModel.persona_id == persona_id)
        )

        prefix: list[MessageModel] = []
        if since_at is not None and lookback > 0:
            lb_q = (
                base.where(MessageModel.created_at <= since_at)
                .order_by(MessageModel.created_at.desc())
                .limit(lookback)
            )
            lb_result = await db.execute(lb_q)
            prefix = list(reversed(lb_result.scalars().all()))

        new_q = base.order_by(MessageModel.created_at)
        if since_at is not None:
            new_q = new_q.where(MessageModel.created_at > since_at)
        new_result = await db.execute(new_q.limit(200))

        return [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in prefix + list(new_result.scalars().all())
        ]
