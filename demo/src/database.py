"""Database engine, session factory, and lifecycle management."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from uuid_utils import uuid7

from .config import settings
from .models.db import Base

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _set_sqlite_pragmas(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _migrate_add_columns(connection):
    """Add columns that create_all skips on existing tables (SQLite limitation)."""
    _ADD_COLUMN_IF_MISSING = [
        ("personas", "membrain_cursor_at", "DATETIME"),
    ]
    for table, column, col_type in _ADD_COLUMN_IF_MISSING:
        result = connection.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result}
        if column not in existing:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            logger.info("Migration: added %s.%s", table, column)


async def init_db() -> None:
    """Initialize the database: ensure directory exists, create engine, create tables."""
    global _engine, _session_factory

    # Ensure the data directory exists
    db_path = Path(settings.LOCAL_DATA_DIR).resolve() / settings.DB_NAME
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(
        settings.DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    # Register PRAGMA listener on the underlying sync engine
    event.listen(_engine.sync_engine, "connect", _set_sqlite_pragmas)

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add columns that create_all won't add to existing tables
        await conn.run_sync(_migrate_add_columns)

    logger.info("Database initialized — tables created/verified (%s)", db_path)


async def close_db() -> None:
    """Dispose the engine connection pool."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection closed")


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession."""
    assert _session_factory is not None, "Database not initialized — call init_db() first"
    async with _session_factory() as session:
        yield session


async def get_session() -> AsyncSession:
    """Create a standalone AsyncSession (for background tasks outside FastAPI DI)."""
    assert _session_factory is not None, "Database not initialized — call init_db() first"
    return _session_factory()


@asynccontextmanager
async def db_session():
    """Async context manager for background tasks outside FastAPI DI."""
    assert _session_factory is not None, "Database not initialized — call init_db() first"
    session = _session_factory()
    try:
        yield session
    finally:
        await session.close()


def new_id() -> str:
    """Generate a new UUID7 string ID."""
    return str(uuid7())
