"""Character preset endpoints."""

import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..models import CharacterPreset

logger = logging.getLogger(__name__)

router = APIRouter()
_characters_cache: list[CharacterPreset] | None = None


def _load_characters() -> list[CharacterPreset]:
    global _characters_cache
    if _characters_cache is not None:
        return _characters_cache
    try:
        path = Path(settings.MANIFESTS_DIR) / "characters.yaml"
        data = yaml.safe_load(path.read_text())
        _characters_cache = [CharacterPreset(**c) for c in data["characters"]]
    except Exception:
        logger.exception("Failed to load characters.yaml")
        _characters_cache = []
    return _characters_cache


@router.get("/characters", response_model=list[CharacterPreset])
async def list_characters():
    """Return all character presets."""
    return _load_characters()


class ImportRequest(BaseModel):
    url: str


class ImportResult(BaseModel):
    character_name: str
    character_bio: str
    neta_uuid: str | None = None
    avatar_img: str | None = None
    header_img: str | None = None


def _parse_nieta_uuid(url: str) -> str:
    """Extract character UUID from a nieta.art URL."""
    parsed = urlparse(url)
    if parsed.hostname not in ("app.nieta.art", "nieta.art", "www.nieta.art"):
        raise HTTPException(status_code=400, detail="URL must be from nieta.art")
    qs = parse_qs(parsed.query)
    uuid = qs.get("uuid", [None])[0]
    if not uuid:
        raise HTTPException(status_code=400, detail="URL missing uuid parameter")
    return uuid


_NIETA_HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "referer": "https://app.nieta.art/",
    "x-platform": "nieta-app/web",
    "x-nieta-app-version": "6.8.4",
    "x-teen-mode": "0",
    "accept": "application/json, text/plain, */*",
}


@router.post("/characters/import", response_model=ImportResult)
async def import_character(body: ImportRequest):
    """Import a character from nieta.art by URL."""
    uuid = _parse_nieta_uuid(body.url)
    api_url = f"https://api.talesofai.cn/v2/travel/parent/{uuid}/profile"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(api_url, headers=_NIETA_HEADERS)
            resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Upstream request timed out") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}") from exc

    data = resp.json()
    bio = data.get("oc_bio") or {}
    name = data.get("name") or "Unknown"

    fields = {
        "Age": bio.get("age"),
        "Role": bio.get("occupation"),
        "Personality": bio.get("persona") or "N/A",
        "Interests": bio.get("interests"),
        "Description": bio.get("description"),
        "Tags": ", ".join(data.get("hashtags") or []) or None,
    }
    character_bio = "\n".join(f"{k}: {v}" for k, v in fields.items() if v is not None)

    config = data.get("config") or {}
    avatar_img = config.get("avatar_img")
    header_img = config.get("header_img")

    return ImportResult(
        character_name=name,
        character_bio=character_bio,
        neta_uuid=uuid,
        avatar_img=avatar_img,
        header_img=header_img,
    )
