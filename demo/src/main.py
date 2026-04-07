"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import make_asgi_app

from .config import settings
from .database import close_db, init_db
from .registry import AgentFactory, TaskRegistry
from .routes import characters, chat, personas, sessions
from .services.membrain_client import MembrainClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle: initialize and tear down the database."""
    logging.basicConfig(level=logging.INFO)
    await init_db()
    registry = TaskRegistry(settings.MANIFESTS_DIR)
    app.state.agent_factory = AgentFactory(registry, settings.LLM_API_URL, settings.LLM_API_KEY)
    app.state.membrain = MembrainClient(
        base_url=settings.MEMBRAIN_BASE_URL,
        timeout=settings.MEMBRAIN_TIMEOUT,
    )

    yield

    await app.state.membrain.close()
    await close_db()


app = FastAPI(
    title="PydanticAI Chat API",
    description="Minimal chat agent with PydanticAI and LiteLLM",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics endpoint
app.mount("/metrics", make_asgi_app())

# Include routers
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(sessions.router, prefix="/api", tags=["sessions"])
app.include_router(characters.router, prefix="/api", tags=["characters"])
app.include_router(personas.router, prefix="/api", tags=["personas"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "pydantic-ai-chat"}


# Serve compiled frontend if dist/ exists (production mode)
_DIST = Path(__file__).parent.parent / "web-app" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        target = (_DIST / full_path).resolve()
        if target.is_file() and target.is_relative_to(_DIST):
            return FileResponse(target)
        return FileResponse(_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=settings.BACKEND_RELOAD,
        reload_dirs=["src"] if settings.BACKEND_RELOAD else None,
    )
