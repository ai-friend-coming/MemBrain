"""Pydantic models for API requests and responses."""

from datetime import datetime

from pydantic import BaseModel


class UIMessagePart(BaseModel):
    """A part of a UI message (text, image, file, etc.)."""

    type: str  # "text", "image", "file", etc.
    text: str | None = None


class UIMessage(BaseModel):
    """A message in AI SDK format."""

    id: str
    role: str  # "user", "assistant", "system"
    parts: list[UIMessagePart]


class ChatRequest(BaseModel):
    """Request payload for the chat endpoint (AI SDK v3 format)."""

    id: str | None = None  # Chat session ID (session_id, UUIDv7)
    messages: list[
        UIMessage
    ]  # Messages from frontend (we'll use last one and load history from storage)
    trigger: str = "submit-message"  # "submit-message" or "regenerate-message"
    characterName: str = ""
    characterBiography: str = ""
    userAlias: str = ""


class SessionMetadata(BaseModel):
    """Metadata for a session."""

    id: str
    title: str
    createdAt: str
    updatedAt: str
    messageCount: int
    preview: str


class SessionDetail(BaseModel):
    """Full session with messages."""

    id: str
    title: str
    messages: list[dict]  # UIMessage format


class TitleGenerationResponse(BaseModel):
    """Response for title generation."""

    title: str


class CharacterPreset(BaseModel):
    """A character preset loaded from the YAML manifest."""

    id: str
    label: str
    alias: str
    biography: str


class PersonaCreate(BaseModel):
    user_alias: str
    character_name: str = ""
    character_biography: str = ""
    neta_uuid: str | None = None
    avatar_img: str | None = None
    header_img: str | None = None
    llm_api_url: str | None = None
    llm_api_key: str | None = None


class PersonaResponse(BaseModel):
    id: str
    user_alias: str
    character_name: str
    character_biography: str
    neta_uuid: str | None = None
    avatar_img: str | None = None
    header_img: str | None = None
    character_reflection: str | None = None
    user_reflection: str | None = None
    llm_api_url: str | None = None
    llm_api_key: str | None = None
    created_at: datetime


class PersonaLLMUpdate(BaseModel):
    llm_api_url: str | None = None
    llm_api_key: str | None = None
