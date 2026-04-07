"""Persona CRUD endpoints."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.schemas import PersonaCreate, PersonaLLMUpdate, PersonaResponse
from ..services import storage

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/personas", response_model=list[PersonaResponse])
async def list_personas(
    x_user_id: str = Header(..., alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
):
    """List all personas for the authenticated user."""
    return await storage.list_personas(db, x_user_id)


@router.post("/personas", response_model=PersonaResponse, status_code=201)
async def create_persona(
    data: PersonaCreate,
    x_user_id: str = Header(..., alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Create a new persona."""
    return await storage.create_persona(db, x_user_id, data)


@router.patch("/personas/{persona_id}/llm", response_model=PersonaResponse)
async def update_persona_llm(
    persona_id: str,
    data: PersonaLLMUpdate,
    x_user_id: str = Header(..., alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Update LLM API settings for a persona."""
    persona = await storage.update_persona_llm(db, persona_id, x_user_id, data)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.delete("/personas/{persona_id}", status_code=204)
async def delete_persona(
    persona_id: str,
    x_user_id: str = Header(..., alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
):
    """Delete a persona and all its sessions and messages."""
    deleted = await storage.delete_persona(db, persona_id, x_user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Persona not found")
    return None
