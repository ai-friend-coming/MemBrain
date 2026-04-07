"""
FastAPI server entry point — app initialization and route registration.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from membrain.api.routes.memory import router as pipeline_router
from membrain.api.routes.viewer import router as viewer_router
from membrain.config import settings
from membrain.infra.db import init_memory_db

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_memory_db()
    except Exception as e:
        _logger.warning("init_memory_db skipped (tables likely exist): %s", e)
    yield
    from membrain.api.manager import search_mgr, task_mgr

    task_mgr.cleanup_all()
    search_mgr.cleanup_all()


app = FastAPI(title="MemBrain API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(viewer_router)
app.include_router(pipeline_router)


def main() -> None:
    """Start uvicorn with mode-appropriate settings.

    Reads BACKEND_MODE from environment / .env:
      dev        — 1 worker + reload  (default)
      evaluation — 1 worker, no reload
      demo       — BACKEND_WORKERS workers, no reload
    """
    from pathlib import Path

    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")

    mode = settings.BACKEND_MODE.lower()
    host = settings.BACKEND_HOST
    port = settings.BACKEND_PORT
    target = "membrain.api.server:app"

    if mode == "dev":
        uvicorn.run(target, host=host, port=port, reload=True, workers=1)
    elif mode == "evaluation":
        uvicorn.run(target, host=host, port=port, reload=False, workers=1)
    elif mode == "demo":
        uvicorn.run(
            target, host=host, port=port, reload=False, workers=settings.BACKEND_WORKERS
        )
    else:
        raise ValueError(
            f"Unknown BACKEND_MODE={mode!r}. Valid values: dev, evaluation, demo"
        )


if __name__ == "__main__":
    main()
