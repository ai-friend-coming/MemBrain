"""Explicit database session scopes for application services."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterable, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

AdvisoryLockKey = tuple[int, int]


@contextmanager
def read_session(session_factory) -> Iterator[Session]:
    """Open a short-lived session for read-only work."""

    db = session_factory()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def write_session(session_factory) -> Iterator[Session]:
    """Open a short-lived session that commits on success."""

    db = session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class TransactionManager:
    """Named read/write scopes for database work."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    @contextmanager
    def read(self) -> Iterator[Session]:
        with read_session(self._session_factory) as db:
            yield db

    @contextmanager
    def write(self) -> Iterator[Session]:
        with write_session(self._session_factory) as db:
            yield db

    @contextmanager
    def advisory_locks(self, keys: Iterable[AdvisoryLockKey]) -> Iterator[Session]:
        """Hold session-level advisory locks for the duration of a workflow.

        Yields the underlying session so callers can reuse it, avoiding
        nested connection checkout against a pool_size=1 engine.
        """
        lock_keys = sorted(set(keys))
        db = self._session_factory()
        try:
            for key1, key2 in lock_keys:
                db.execute(
                    text("SELECT pg_advisory_lock(:key1, :key2)"),
                    {"key1": key1, "key2": key2},
                )
            yield db
        finally:
            for key1, key2 in reversed(lock_keys):
                db.execute(
                    text("SELECT pg_advisory_unlock(:key1, :key2)"),
                    {"key1": key1, "key2": key2},
                )
            db.close()
