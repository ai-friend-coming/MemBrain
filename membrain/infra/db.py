"""Database engine, session factory, and schema initialisation."""

from __future__ import annotations

import logging
import re

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from membrain.config import settings

log = logging.getLogger(__name__)

_SAFE_SCHEMA_RE = re.compile(r"^[a-zA-Z0-9_]+$")


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


_memory_table_names_cache: set[str] | None = None


def _get_memory_table_names() -> set[str]:
    """Auto-derive per-task table names from models with info={'per_task': True}."""
    global _memory_table_names_cache
    if _memory_table_names_cache is not None:
        return _memory_table_names_cache
    import membrain.infra.models.memory  # noqa: F401

    _memory_table_names_cache = {
        t.name for t in Base.metadata.sorted_tables if t.info.get("per_task", False)
    }
    return _memory_table_names_cache


engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
)
SessionLocal = sessionmaker(bind=engine)


@event.listens_for(engine, "connect")
def _set_global_statement_timeout(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute(f"SET statement_timeout = {int(settings.DB_STATEMENT_TIMEOUT)}")
    cursor.close()


def create_run_engine(task_pk: int, run_tag: str, url: str | None = None):
    """Create an engine scoped to a run-isolated schema ``task_{pk}__{run_tag}``.

    Every connection has ``search_path`` set to ``task_{pk}__{run_tag}, public``
    so unqualified table names resolve to this run's schema, falling back to
    ``public`` for shared dataset tables.
    """
    schema = f"task_{int(task_pk)}__{run_tag}"
    return _make_engine_with_schema(schema, url)


def _make_engine_with_schema(schema: str, url: str | None = None):
    """Internal: create a worker engine with a fixed search_path."""
    if not _SAFE_SCHEMA_RE.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema!r}")
    eng = create_engine(
        url or settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=settings.DB_WORKER_POOL_SIZE,
        max_overflow=settings.DB_WORKER_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
    )

    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {schema}, public")
        cursor.execute(f"SET statement_timeout = {int(settings.DB_STATEMENT_TIMEOUT)}")
        cursor.close()

    return eng


def _ensure_database_exists() -> None:
    """Connect to the 'postgres' system DB and create target DB if it doesn't exist.

    This is idempotent; no-op if the database already exists.
    """
    target = settings.DB_NAME
    system_url = (
        f"postgresql+psycopg2://{settings.DB_USER}:{settings.DB_PASSWD}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}/postgres"
    )
    sys_eng = create_engine(system_url, pool_pre_ping=True, pool_size=1)
    try:
        with sys_eng.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target},
            ).first()
            if not exists:
                try:
                    conn.execute(text(f'CREATE DATABASE "{target}"'))
                    log.info("Created database %r", target)
                except IntegrityError:
                    log.debug(
                        "Database %r already exists (concurrent creation skipped)",
                        target,
                    )
    finally:
        sys_eng.dispose()


def init_db() -> None:
    """Create all tables that don't already exist (excludes per-task memory tables)."""
    _ensure_database_exists()
    public_tables = [
        t
        for t in Base.metadata.sorted_tables
        if t.name not in _get_memory_table_names()
    ]
    Base.metadata.create_all(bind=engine, tables=public_tables)


def init_run_schema(eng, task_pk: int, run_tag: str) -> None:
    """Create run-isolated schema ``task_{pk}__{run_tag}`` and memory tables + indexes."""
    schema = f"task_{int(task_pk)}__{run_tag}"
    _create_schema_and_tables(eng, schema)


def _create_schema_and_tables(eng, schema: str) -> None:
    """Internal: create schema, memory tables, and supplemental indexes."""
    if not _SAFE_SCHEMA_RE.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema!r}")
    import membrain.infra.models.memory  # noqa: F401

    with eng.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()

    memory_tables = [
        t for t in Base.metadata.sorted_tables if t.name in _get_memory_table_names()
    ]
    Base.metadata.create_all(eng, tables=memory_tables)

    with eng.connect() as conn:
        _create_memory_indexes(conn, schema)
        conn.execute(
            text(
                "ALTER TABLE entity_tree_nodes "
                "ADD COLUMN IF NOT EXISTS uncertainty_score FLOAT DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE entity_tree_nodes "
                "ADD COLUMN IF NOT EXISTS support INTEGER DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE entity_tree_nodes "
                "ADD COLUMN IF NOT EXISTS fresh_count INTEGER DEFAULT 0"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE entity_tree_nodes "
                f"ADD COLUMN IF NOT EXISTS subtree_centroid halfvec({settings.EMBED_DIM})"
            )
        )
        # Ensure fact_id FK has ON DELETE CASCADE (idempotent migration).
        conn.execute(
            text(
                "ALTER TABLE facts "
                "ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'"
            )
        )
        conn.execute(
            text("""
            DO $$
            DECLARE _con text;
            BEGIN
                SELECT c.conname INTO _con
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                WHERE c.conrelid = 'entity_tree_nodes'::regclass
                  AND c.contype = 'f' AND a.attname = 'fact_id'
                  AND c.confdeltype != 'c';
                IF _con IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE entity_tree_nodes DROP CONSTRAINT '
                            || _con;
                    EXECUTE 'ALTER TABLE entity_tree_nodes ADD CONSTRAINT '
                            || _con
                            || ' FOREIGN KEY (fact_id) REFERENCES facts(id)'
                            || ' ON DELETE CASCADE';
                END IF;
            END $$;
        """)
        )
        conn.commit()


# Advisory lock key for serialising BM25 CREATE INDEX across workers.
_BM25_ADVISORY_LOCK_KEY = 0x4D42_BF25  # "MB_BF25"


def _create_memory_indexes(conn, schema: str) -> None:
    """Create supplemental indexes for memory tables (BM25, partial unique, FK).

    BM25 indexes use a shared ParadeDB catalog that deadlocks under concurrent
    CREATE INDEX + INSERT.  We serialise the BM25 block with a session-level
    advisory lock (released at COMMIT).
    """
    s = schema  # short alias for index names
    conn.execute(text(f"SELECT pg_advisory_xact_lock({_BM25_ADVISORY_LOCK_KEY})"))
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__idx_fact_refs_alias_bm25 "
            "ON fact_refs USING bm25 (id, alias_text) WITH (key_field='id')"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__idx_facts_search_text_bm25 "
            "ON facts USING bm25 (id, (search_text::pdb.simple('stemmer=english'))) "
            "WITH (key_field='id')"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__idx_session_summaries_bm25 "
            "ON session_summaries USING bm25 "
            "(id, (content::pdb.simple('stemmer=english'))) "
            "WITH (key_field='id')"
        )
    )
    conn.execute(
        text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {s}__uq_tree_leaf_fact_partial "
            "ON entity_tree_nodes (task_id, entity_id, fact_id) "
            "WHERE fact_id IS NOT NULL"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__ix_time_annotations_fact "
            "ON time_annotations (fact_id)"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__ix_fact_refs_fact ON fact_refs (fact_id)"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__ix_fact_refs_entity ON fact_refs (entity_id)"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__ix_facts_batch_index "
            "ON facts (task_id, batch_index)"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__ix_facts_session "
            "ON facts (task_id, session_number)"
        )
    )
    conn.execute(
        text(f"CREATE INDEX IF NOT EXISTS {s}__ix_facts_batch_id ON facts (batch_id)")
    )
    # HNSW vector indexes (columns are halfvec; inner-product for normalized vectors)
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__idx_entities_desc_embedding_hnsw "
            "ON entities USING hnsw (desc_embedding halfvec_ip_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__idx_facts_text_embedding_hnsw "
            "ON facts USING hnsw (text_embedding halfvec_ip_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {s}__idx_tree_desc_embedding_hnsw "
            "ON entity_tree_nodes USING hnsw (description_embedding halfvec_ip_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
    )


def init_memory_db() -> None:
    """Create public-schema tables (dataset + coordination) and indexes."""
    _ensure_database_exists()
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()

    import membrain.infra.models.dataset  # noqa: F401
    import membrain.infra.models.memory  # noqa: F401

    public_tables = [
        t
        for t in Base.metadata.sorted_tables
        if t.name not in _get_memory_table_names()
    ]
    Base.metadata.create_all(bind=engine, tables=public_tables)

    with engine.connect() as conn:
        conn.execute(
            text(
                "ALTER TABLE tasks "
                "ADD COLUMN IF NOT EXISTS agent_profile VARCHAR(64) NULL"
            )
        )
        conn.commit()

    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_bm25 "
                "ON chat_messages USING bm25 "
                "(id, (content::pdb.simple('stemmer=english'))) "
                "WITH (key_field='id')"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chat_sessions_task_id "
                "ON chat_sessions (task_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chat_messages_session_id "
                "ON chat_messages (session_id)"
            )
        )
        qa_pairs_exists = conn.execute(
            text("SELECT to_regclass('public.qa_pairs')")
        ).scalar()
        if qa_pairs_exists is not None:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_qa_pairs_task_id "
                    "ON qa_pairs (task_id)"
                )
            )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tasks_dataset_id ON tasks (dataset_id)")
        )
        conn.commit()
