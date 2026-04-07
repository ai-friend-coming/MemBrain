# Concurrency Architecture & Connection Pool Design

## Overview: Engine Layering

There are **4 independent types of SQLAlchemy Engines** in the system, each taking on different responsibilities without preempting connections.

```
┌──────────────────────────────────────────────────────────────────┐
│  PostgreSQL max_connections = 150                                │
│                                                                  │
│  Main Engine (SessionLocal)        pool_size=5  overflow=10      │
│  ├── Frontend Viewer GET routes (SET LOCAL search_path switches schema) │
│  ├── CLI ls / delete / dataset                                   │
│  └── evaluation/memory.py helper queries (session count, PKs, etc.) │
│                                                                  │
│  Per-Run Engine (LocalRunner)      pool_size=1  overflow=0       │
│  └── Created on-demand, one for each exp run worker thread       │
│      schema: task_{pk}__{run_tag}                                │
│                                                                  │
│  Search Engine                     pool_size=20 overflow=10      │
│  ├── [Local] LocalSearchRunner  ← Shared by QA eval worker threads │
│  └── [HTTP] SearchServiceManager ← Frontend search + API search  │
│      Both use SET LOCAL search_path to switch schema, read-only  │
│                                                                  │
│  Conversation Ingestion Engine (TaskServiceManager)              │
│  └── Same structure as Per-Run Engine, used for write in HTTP session mode │
│                                                                  │
│  ⚠️ per_task tables do **NOT exist** in the public schema        │
│  (entities, facts, entity_tree_nodes, etc.)                      │
│  They are only created and queried within the task_{pk}__{run_tag} schema │
└──────────────────────────────────────────────────────────────────┘
```

---

## Detailed Engine Breakdown

### 1. Main Engine — `SessionLocal`

**Definition location**: `membrain/infra/db.py:40-48`

- `pool_size=5, max_overflow=10` → max 15 connections
- All connections automatically set `statement_timeout=300000ms` on the `connect` event
- Prevents stale connections via `pool_pre_ping=True`
- **Users**:
  - Viewer GET routes (`Depends(get_db)`) — switches to the run schema before querying via `SET LOCAL search_path TO task_{pk}__{run_tag}, public`
  - `evaluation/memory.py` — helper functions like `get_session_count`, `load_session_messages`, `_get_session_pk`, `_find/create/update_pipeline_run`
  - `evaluation/qa.py` — `load_qa_pairs`
  - `membrain/infra/checkpoint.py` — `_task_id_from_pk`
- **`SET LOCAL` Safety**: psycopg2 starts transactions by default (autocommit=False). The autobegin of a Session guarantees that `SET LOCAL` and subsequent queries execute within the same transaction. The lifecycle of `with sf() as db:` / `Depends(get_db)` guarantees the transaction boundaries.

> ⚠️ **Important**: `SET LOCAL search_path` is reliable within the scope of `with session:` or `Depends(get_db)`,
> provided that **`db.commit()` is not called** throughout the handler (read-only routes do not need commit).
> The **per_task tables do not exist** in the public schema, so there is no risk of a "silent fallback to public" —
> if the run schema is missing a table, the query will immediately throw an error.

### 2. Per-Run Engine — `create_run_engine()`

**Definition location**: `membrain/infra/db.py:85-93`

- `pool_size=1, max_overflow=0` → Exactly 1 connection per engine (sufficient, no nested sessions)
- `search_path` is fixed to `task_{pk}__{run_tag}, public` on the `connect` event
- **Users**: `evaluation/runtime/local_runner.py` — instantiated by `LocalRunner.__init__` per `(task_pk, run_tag)`
- **Lifecycle**: Bound to the `LocalRunner` instance; `engine.dispose()` is called via `runner.cleanup()` when the worker thread finishes
- **Thread Isolation**: Each worker thread owns an independent instance, with no shared state

> ⚠️ **Important**: `pool_size=1` is only suitable for single-writer scenarios (one runner per worker thread).
> **Must not be used for concurrent reads** (such as viewer or search), otherwise requests will serialize and potentially timeout.

### 3. Search Engine — Two usage patterns, shared parameters

**Local path** (`evaluation/runtime/local_search.py` — `LocalSearchRunner`):
- `pool_size=QA_SEARCH_POOL_SIZE(20), max_overflow=10`
- **Shared single instance for QA eval worker threads** (read-only, thread-safe)
- `search()` is a standard synchronous method, called directly within `ThreadPoolExecutor`
- Each call switches to the target run schema via `SET LOCAL search_path`

**HTTP path** (`membrain/api/routes/memory.py` — `SearchServiceManager`):
- Perfectly identical parameters: `pool_size=20, max_overflow=10`
- **Frontend search + API search** go through here: `POST /api/memory/search`
- Each call switches to the target run schema via `SET LOCAL search_path TO task_{pk}__{run_tag}, public`
- `search_mgr` is an in-process singleton, lazily initialized (engine created on first request)
- Lifecycle: Bound to the uvicorn process; `cleanup_all()` is called on `lifespan` shutdown

**Both paths reuse the exact same backend functions** (`retrieve_facts`, `agentic_retrieve_facts`, `budget_pack`, etc.),
the only difference being who manages the engine and clients.

> ⚠️ The Search Engines of the two paths **do not share** a connection pool — the local path and HTTP path create independent engines.
> Thus, if running QA eval + frontend conversation searches simultaneously, search connections can spike up to `(20+10) × 2 = 60`.

### 4. Conversation Ingestion Engine — `TaskServiceManager`

**Definition location**: `membrain/api/manager.py`

- Similar structure as the Per-Run Engine (calls `create_run_engine()`), lazily initialized by `task_pk` and cached, with run_tag fixed to `"default"`
- **Write**: Via `POST /api/memory` (store+digest) → `TaskServiceManager.get_or_create()` → `SessionMemoryWorkflow.process_session()`
- **Search**: During frontend Q&A, routes via `POST /api/memory/search` → `SearchServiceManager` (see above)
- Both reside in the same uvicorn process. **Ingestion and search use different engines and different handlers**, connection pools do not interfere
- `task_mgr.cleanup(task_pk)` releases resources after all pending sessions are digested

---

## Critical Concurrency Decisions

### exp run: Directly call MemoryService (Bypassing HTTP)

**Root Cause**: Under the HTTP mode, the `async def ingest_session()` handler contains numerous synchronous blocking operations
(sync SQLAlchemy + sync httpx embedding), freezing the uvicorn event loop and causing multi-worker requests to serialize.

**Solution**: `evaluation/runtime/local_runner.py` — `LocalRunner`
- Each worker thread drives async logic using `asyncio.new_event_loop()` + `loop.run_until_complete()`
- Each thread has an independent event loop, independent from each other
- Entry point: `evaluation/memory.py:_process_task()`

### QA eval: Shared LocalSearchRunner (Bypassing HTTP)

**Problem**: Same as above, embedding + DB queries inside the `search_memory()` handler are all synchronous operations.

**Solution**: `evaluation/runtime/local_search.py` — `LocalSearchRunner`
- Shared single instance; `search()` is a regular synchronous function, directly executed in `ThreadPoolExecutor` worker threads
- Connection pool (pool=20) guarantees concurrency

## Full Request Path in Session Mode

```
Frontend user submits chat (POST /api/memory, store=true digest=true)
  └── memory.py
      ├── Writes to chat_sessions / chat_messages       [Main Pool SessionLocal]
      └── digest: task_mgr.get_or_create()
          └── SessionMemoryWorkflow.process_session()   [Conversation Ingestion Engine]
              ├── summarize_session()
              ├── ingest_session()
              └── invalidate_facts()

Frontend user queries (POST /api/memory/search)
  └── memory.py: search_memory()
      └── search_mgr.get_session_factory()              [SearchServiceManager Engine]
          ├── SET LOCAL search_path → run schema
          ├── retrieve_facts() + embed_client
          ├── build_aspect_paths()
          └── retrieve_sessions()
```

**Key takeaway**: Ingestion and search use completely independent engines, without blocking each other.
However, both run as `async def` on the same uvicorn event loop,
so internal synchronous DB operations (not yet migrated to `asyncio.to_thread`) will still serialize.

---

## Known Issues Log

### Resolved

| Issue | File | Fix Approach |
|-------|------|--------------|
| `init_db()` created per_task tables in the public schema | `db.py` | Updated `init_db()` to filter via `_get_memory_table_names()`, only creating non-per_task tables; DROPPED lingering per_task tables in public |
| `init_run_schema`'s `create_all(checkfirst)` falsely detected table existence | `db.py` | Same root cause as above: per_task tables existed in public → search_path found them in public → skipped creation in run schema. Fixed along with `init_db()` |
| Global BM25 index name collision + deadlock | `db.py` | Added schema prefix to index names (`{schema}__idx_*`); serialized BM25 CREATE INDEX with `pg_advisory_xact_lock`, automatically released upon transaction end |
| run_tag containing hyphens `-` resulted in invalid schema names | `evaluation/cli.py` | `sanitized` got an additional `.replace("-", "_")`; added `_validate_run_tag()` to validate user input |
| Dataset deletion blocked by FK (qa_pairs, chat_sessions) | `evaluation/models/qa.py`, `membrain/infra/models/dataset.py` | Added `ondelete="CASCADE"` to `qa_pairs.task_id` and `chat_sessions.task_id` |
| `_clear_memory_for_tasks` failed to match run schema | `evaluation/cli.py` | Switched to `LIKE 'task_{tid}__%'` querying `information_schema.schemata`, dropped with quotes |
| Pipeline progress logs leaked to CLI main thread | `membrain/memory/application/batch_ingestor.py` | Replaced all `print()` with `log.debug()`, captured by per-task FileHandler |
| Sync blocking froze event loop inside async handler | routes | exp run / QA pivoted to local execution, HTTP path is low-priority pending fix |
| subprocess.run could hang indefinitely without timeout | `checkpoint.py` | Added `timeout=120` |
| exp run worker strictly depended on a running uvicorn backend | `evaluation/memory.py` | Switched to `LocalRunner`, no longer requiring the backend |
| QA eval multi-thread search serialized | `evaluation/qa.py` | Switched to `LocalSearchRunner` |
| CLI list/delete depended on a running uvicorn backend | `evaluation/cli.py` | Routes directly via DB |
| checkpoint `save_checkpoint()` invoked subprocess within HTTP handler | `membrain/api/routes/memory.py` | Removed checkpoint invocation from HTTP ingestion path; checkpoint is now strictly used in evaluation LocalRunner |

### Potential Risks (Unresolved)

1. **Lockless lazy initialization of `SearchServiceManager._ensure_engine()`** (`membrain/api/manager.py`)
   — Extremely low probability race condition in HTTP mode; minimal impact

2. **`time.sleep()` blocks the event loop within an async context in `embedding.py`**
   — Triggered only during embedding service failure retries, affects HTTP mode

---

## Connection Budget

```
Scenario: exp run max-workers=5 + QA workers=5 + frontend session mode used concurrently

Main Pool:                  ≤ 15 connections (viewer GET + CLI)
Per-run (ingestion):        5 × 1 = 5 connections
LocalSearchRunner:          ≤ 5 connections  (Actual QA workers concurrency, max 30)
_SearchServiceManager:      ≤ 5 connections  (Frontend search, max 30)
Conversation Ingestion:     ≤ 5 connections  (Session writes, 1 connection per task)

Total:                      ≤ 35 connections (Far below max_connections=150)
```

> Extreme case (fully loaded): 15 + 5 + 30 + 30 + 5 = 85 connections, still yielding a margin.
> ⚠️ When exp run max-workers=50, Per-run connections = 50, plus the initialization phase
> of `_create_schema_and_tables` temporarily consuming 1 extra connection, hitting up to ~65+.
> Ensure no massive search connections occur synchronously during this window.

---

## File Index

| Responsibility | File |
|----------------|------|
| Engine / SessionLocal definition | `membrain/infra/db.py` |
| Connection pool parameters | `membrain/config.py` (`DB_POOL_SIZE`, etc.) |
| Ingestion executing locally | `evaluation/runtime/local_runner.py` |
| QA Search executing locally | `evaluation/runtime/local_search.py` |
| Ingestion pipeline entry | `evaluation/memory.py` |
| QA pipeline entry | `evaluation/qa.py` |
| Session mode search (HTTP) | `membrain/api/routes/memory.py` (`SearchServiceManager`) |
| Session mode write (HTTP) | `membrain/api/manager.py` (`TaskServiceManager`) |
| Frontend Viewer routes | `membrain/api/routes/viewer.py` |
| Checkpoint (pg_dump/restore) | `membrain/infra/checkpoint.py` |
