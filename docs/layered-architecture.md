# Domain × Layer 2D Design Principles

This document records the structural design conventions of the current MemBrain codebase. The goal is to clearly separate:

- Business use case orchestration
- Pure algorithm / domain logic
- Database and external system details

This prevents different responsibilities from being mixed in the same layer.

---

## Goals

This design primarily solves 4 problems:

1. Prevent core algorithms from directly depending on database transactions, rollbacks, ORMs, and locks.
2. Provide explicit entry points for use cases like ingestion, summarization, and entity tree updates.
3. Centralize infra details, avoiding SQL, session management, and persistence mappings from scattering throughout algorithmic code.
4. Improve maintainability without breaking checkpoint / resume and existing performance characteristics.

---

## Two Dimensions

The code organization aligns along two dimensions:

### Dimension 1: Feature Domains

The code is first functionally divided into two top-level domains:

- **`memory/`**: Memory ingestion domain (ingestion pipeline)
- **`retrieval/`**: Retrieval domain (search / ranking pipeline)

Both domains share the same **`infra/`** layer acts as a unified interface to the outside world.

### Dimension 2: Architecture Layers

Within each domain, the code is further divided by **architectural layer**:

- `core/`: Pure algorithms / domain logic, independent of any external systems
- `application/`: Use case orchestration, combining core and infra layers

`infra/` is a top-level shared layer and does not belong to any single domain.

---

## Current Directory Structure

```text
membrain/
├── memory/
│   ├── core/
│   │   ├── entity_tree/        # Entity Tree full suite of algorithms (routing, propagation, auditing, etc.)
│   │   └── entity_resolver.py  # Three-layer entity deduplication algorithm
│   └── application/
│       ├── batch_ingestor.py
│       ├── entity_tree_updater.py
│       ├── ingest_workflow.py
│       ├── message_text.py
│       ├── session_memory_workflow.py
│       └── session_summarizer.py
│
├── retrieval/
│   ├── core/
│   │   ├── budget_pack.py   # Token budget packing (pure data transformation)
│   │   └── types.py
│   └── application/
│       └── retrieval.py     # Contains RRF, Re-rank, and Agentic retrieval workflows
│
├── api/                     # Unified HTTP API server and routes
│   ├── routes/
│   └── schemas/
│
├── agents/                  # Cross-domain, an extension of infra for LLM calls
│   ├── factory.py
│   ├── manifest.py
│   ├── registry.py
│   └── retry.py
│
└── infra/                   # Shared by all domains
    ├── db.py & models/      # SQLAlchemy basics & definitions
    ├── transaction_manager.py
    ├── checkpoint.py        # Run state persistence (pg_dump/restore)
    ├── persistence/         # ORM mapper + batch_writer
    ├── queries/             # DB query functions (memory domain)
    ├── retrieval/           # DB operations related to retrieval
    └── clients/             # HTTP/LLM clients (embedding, rerank, query rewriters)
```

---

## Responsibilities of Each Layer

### `core/` (Internal to Each Domain)

The pure domain / algorithm layer. It remains in-memory, predictable, and reusable.

**It is responsible for:**
- Pure data structures
- Pure algorithm steps
- Computational logic independent of the database

**It is NOT responsible for:**
- `Session`, SQL, advisory locks, ORM models
- `db.commit()` / `db.rollback()`
- Any external system calls

> Core is responsible for "Given in-memory inputs, calculate the correct output," without caring which table those inputs come from.

### `application/` (Internal to Each Domain)

The use case orchestration layer.

**It is responsible for:**
- Defining a complete use case
- Determining execution order
- Combining core algorithms and infra adapters
- Deciding at which steps to call LLMs, embeddings, and persistence

**It is NOT responsible for:**
- Writing raw SQL
- Implementing underlying transactional primitives
- Saving the pure algorithm data structures

> Application is responsible for "What needs to be done and in what order," not "How the database does it".

### `infra/` (Top-Level Shared)

The infrastructure layer, responsible for all "real-world" operational details.

**It is responsible for:**
- DB connections and session scope
- Transactions / advisory locks
- ORM models
- SQL queries
- Persistence mappers (loading/saving memory models to and from the DB)
- HTTP clients (embedding, rerank)
- LLM call adapters (query rewriting)

**It is NOT responsible for:**
- Dictating the sequence of business use cases
- Implementing pure algorithmic rules

> Infra is responsible for "How to interact with the database / external systems," not "How a usecase should run overall".

---

## Dependency Direction

```text
api / evaluation / runtime
          ↓
memory/application    retrieval/application
          ↓                    ↓
memory/core          retrieval/core
          ↓                    ↓
                 infra/
```

**Specific Rules:**

- `api` and `evaluation` call `memory/application` or `retrieval/application`.
- `application` can call its domain's `core` and the shared `infra`.
- `core` MUST NOT depend on `infra` (there should be no infra imports at runtime).
- `infra` can depend on models from `memory/core` for loading, saving, and mapping.

**A Practical Rule of Thumb:**

- If the code needs a DB `Session`, it generally belongs in `infra`.
- If the code primarily dictates "sequence of steps", it belongs in a domain's `application`.
- If the code can run completely isolated from a database, it belongs in a domain's `core`.
- If the code is an HTTP client or DB query, it belongs in `infra`.

### `agents/` (Cross-domain, extension of infra)

`agents/` does not belong to any single feature domain and has no `core/application` sub-layers.

It serves as an adaptation layer for the LLM runtime:
- `factory.py` binds to PydanticAI / OpenAI to construct specific agent instances.
- `registry.py` loads manifests, templates, and tool schemas.
- `retry.py` handles retry logic for LLM calls.

Architecturally, `agents/` is kin to `infra/clients/` (HTTP embedding/rerank clients) — they are both "external service adapters." However, since LLM agent calling is vastly more complex than simple HTTP clients (involving manifests, tool schemas, output structuring), separating it into its own directory is justified.

Algorithm functions inside `memory/core/entity_tree/` (e.g., `audit.py`, `propagate.py`, `pipeline.py`) use `agents/` directly (see discussion below).

---

## Known Architecture Trade-offs

### LLM Algorithms in `memory/core`

Functions like `audit.py`, `propagate.py`, and `pipeline.py` in `memory/core/entity_tree/` initiate LLM calls via `agents/`. This may appear to violate the "core does not depend on external systems" rule, but it is an intentional design decision:

**Why are LLM calls kept in core?**

For auditing and propagating the Entity Tree, the LLM *is* the algorithm itself:
- `run_budgeted_audit`: The LLM decides whether to split nodes and restructure the tree.
- `propagate_with_reachability_gate`: The LLM generates node summaries and decides propagation paths.

This differs from "Calling the DB to fetch data, then computing." The LLM is not an I/O source but rather the computing engine (similar to using numpy for matrix operations). These functions:
- Do not write to the DB.
- Do not manage connection lifecycles.
- Take in-memory inputs and return in-memory results.

**Known Compromise:**

`compute_entity_tree_updates` in `pipeline.py` is an orchestration function (coordinating embed → route → audit → propagate → pack), which strictly belongs in the `application` layer. However, because its output structures (like `EntityTreeUpdateResult`) are directly depended on by `infra/persistence/entity_tree_store.py`, moving it to `application` would create an inverted dependency from `infra` to `application`. We accept this compromise temporarily until the typing dependencies in `infra` are further decoupled.

### SQL within `retrieval.py`

`retrieval/application/retrieval.py` contains some direct `db.execute()` calls. According to the document principles, SQL should belong in `infra/`.

This is a known exception: some SQL queries are highly coupled with PostgreSQL / Tantivy full-text search syntax (like `pdb.parse()`, `pdb.score()`), functioning as an intrinsic part of the retrieval strategy itself rather than generic persistence operations. Moving them out to `infra/` would create meaningless indirection. They remain intact until there's a strong rationale to abstract them.

---

## Case Study: Entity Tree Layering

Entity Tree is the central data structure in the `memory` domain and serves as a classic example of this layered design.

### What Stays in `memory/core/entity_tree/`

- `EntityTree` / `TreeNode` memory models
- Routing, attaching, propagating, auditing, and structural tree operations
- Text rendering, vector similarity logic, and pre-grouping

### What Stays in `infra/persistence/`

- Batch loading trees from the DB
- Persistence identity tracking
- Node save/delete mapping operations
- pgvector KNN queries
- Entity/fact/tree batch queries
- Advisory locks and transaction scopes

### What Stays in `memory/application/entity_tree_updater.py`

```python
EntityTreeUpdater
  -> EntityTreeStore.load_update_state()
  -> memory.core.entity_tree.pipeline.compute_entity_tree_updates()
  -> EntityTreeStore.apply_updates()
```

**Benefits:**
- Algorithm logic is ignorant of the `entity_tree_nodes` table structure.
- `persistence` can independently optimize batch read/write operations.
- Transaction / lock strategies can be adjusted in `infra` without poisoning `core`.

---

## Transaction Principles

Transactions should not be obscured under vague names that conceal their boundaries from higher layers. Recommended principles:

- Use explicitly named transaction scopes.
- Differentiate between `read` and `write`.
- Explicitly express advisory locks.
- The existence of transactions belongs to `infra`, not `core` or `application`.

Current corresponding implementation: `membrain/infra/transaction_manager.py`

---

## Performance Principles

Layering must not come at the cost of noticeable performance degradation:

1. Batch read/write optimizations should ideally reside in `infra`.
2. `application` should orchestrate batch interfaces instead of making iterative individual calls.
3. `core` maintains linear or local computations on inputs without introducing meaningless copying.

---

## Checkpoint / Resume Principles

Checkpoint/resume provides system runtime resilience and does not belong in `core`:

- Checkpoint state management should not be baked into algorithm objects.
- `core` MUST NOT depend on checkpoint files or restoration logic.
- The overarching runtime drives the restart flow via the `application` layer.

Current implementation: `membrain/infra/checkpoint.py`

---

## Where Should New Code Go?

First, determine the feature domain, then determine the layer:

**Step 1: Which Domain?**
- Ingestion-related (fact extraction, entity resolution, entity tree updates) → `memory/`
- Retrieval-related (retrieval pipelines, ranking strategy, context packing) → `retrieval/`
- Direct DB connections, HTTP clients, ORM → `infra/` (Belongs to no single domain)
- HTTP API endpoints and routes → `api/`
- LLM Agent logic or client setups → `agents/`

**Step 2: Which Layer Inside the Domain?**

Place in `core/` if:
- It mainly handles in-memory objects, not requiring a DB or HTTP.
- It can run entirely isolated from the database.

Place in `application/` if:
- It serves as the entry point for a completed use case.
- It orchestrates multiple adapters or algorithms.
- It needs to decide the sequencing of steps.
