"""Session CRUD endpoints."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import SessionDetail, SessionMetadata, TitleGenerationResponse
from ..services import storage

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions")
async def create_session(
    persona_id: str = Header(..., alias="X-Persona-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Create a new empty session. Returns { id }."""
    session_id = await storage.create_session(db, persona_id)
    return {"id": session_id}


@router.get("/sessions", response_model=list[SessionMetadata])
async def list_sessions(
    persona_id: str = Header(..., alias="X-Persona-ID"),
    db: AsyncSession = Depends(get_db),
):
    """List all sessions for a user, sorted by updatedAt desc."""
    return await storage.list_sessions(db, persona_id)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    persona_id: str = Header(..., alias="X-Persona-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Get a session with all messages in UI format."""
    detail = await storage.get_session_detail(db, persona_id, session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session_endpoint(
    session_id: str,
    persona_id: str = Header(..., alias="X-Persona-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Delete a session."""
    deleted = await storage.delete_session(db, persona_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return None


@router.delete("/user/data", status_code=204)
async def delete_user_data_endpoint(
    persona_id: str = Header(..., alias="X-Persona-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Delete all sessions and messages for the current user."""
    await storage.delete_persona_data(db, persona_id)
    return None


class TitleUpdateRequest(BaseModel):
    title: str


@router.patch("/sessions/{session_id}/title", response_model=TitleGenerationResponse)
async def update_session_title(
    session_id: str,
    request: TitleUpdateRequest,
    persona_id: str = Header(..., alias="X-Persona-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Update session title."""
    ok = await storage.update_title(db, persona_id, session_id, request.title)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return TitleGenerationResponse(title=request.title)


@router.delete("/sessions/{session_id}/messages/{message_id}", status_code=204)
async def delete_messages_from_endpoint(
    session_id: str,
    message_id: str,
    persona_id: str = Header(..., alias="X-Persona-ID"),
):
    """Delete a message and all subsequent messages in the session."""
    found = await storage.delete_messages_from(session_id, message_id)
    if not found:
        raise HTTPException(status_code=404, detail="Message not found")
    return None
