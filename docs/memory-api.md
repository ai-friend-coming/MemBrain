# Memory API Reference

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/memory` | POST | Store raw messages, digest pending sessions, or both |
| `/api/memory/search` | POST | Search memory for a question |

---

## POST /api/memory

Accepts new conversation messages and/or triggers memory digestion — the process that extracts structured facts, resolves entities, and builds the hierarchical entity tree.

### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `dataset` | string | yes | — | Namespace for a user or persona (e.g. `"lab_member_001_okabe"`) |
| `task` | string | yes | — | Sub-namespace for a conversation context (e.g. `"casual_chat_with_amadeus"`) |
| `messages` | array | if `store=true` | `[]` | Ordered list of messages in this session |
| `session_time` | string | no | `""` | ISO 8601 timestamp representing the session start |
| `store` | bool | no | `true` | Persist `messages` as a new raw session |
| `digest` | bool | no | `true` | Trigger memory digestion on all pending undigested sessions |
| `agent_profile` | string | no | `null` | Optional persona profile injected into LLM extraction prompts |

**Message object fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `speaker` | string | yes | Display name of the speaker |
| `content` | string | yes | Message text |
| `message_time` | string | no | ISO 8601 timestamp for this individual message |

### Modes

The three operating modes are controlled by the `store` and `digest` flags.

#### `store=true, digest=false` — Store only

Persists the messages as a new session without triggering digestion. Useful when batching multiple sessions before a single digest pass.

```bash
curl -X POST "http://localhost:9574/api/memory" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "messages": [
      {
        "speaker": "Okabe",
        "content": "I ended up buying Dr. Pepper again today. Even though it tastes like carbonated cough syrup, I cannot seem to stop drinking it while debugging.",
        "message_time": "2025-12-05T14:00:00+00:00"
      },
      {
        "speaker": "Amadeus",
        "content": "Well, obviously. It is the intellectual drink for the chosen ones. Not that I would expect someone with your lacking taste buds to truly appreciate its complex flavor profile. Hmph.",
        "message_time": "2025-12-05T14:02:00+00:00"
      }
    ],
    "store": true,
    "digest": false
  }'
```

Response `status`: `"stored"`

#### `store=true, digest=true` — Store and digest (default)

Persists the session and immediately enqueues background digestion of all unprocessed sessions. This is the standard mode for real-time conversations.

```bash
curl -X POST "http://localhost:9574/api/memory" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "messages": [
      {
        "speaker": "Okabe",
        "content": "I ended up buying Dr. Pepper again today. Even though it tastes like carbonated cough syrup, I cannot seem to stop drinking it while debugging.",
        "message_time": "2025-12-05T14:00:00+00:00"
      },
      {
        "speaker": "Amadeus",
        "content": "Well, obviously. It is the intellectual drink for the chosen ones. Not that I would expect someone with your lacking taste buds to truly appreciate its complex flavor profile. Hmph.",
        "message_time": "2025-12-05T14:02:00+00:00"
      }
    ],
    "store": true,
    "digest": true
  }'
```

Response `status`: `"stored_and_digest_queued"`

#### `store=false, digest=true` — Digest only

Triggers digestion on all existing unprocessed sessions without adding new data. Useful for reprocessing or when sessions were pre-loaded via another path.

```bash
curl -X POST "http://localhost:9574/api/memory" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "store": false,
    "digest": true
  }'
```

Response `status`: `"digest_queued"`

### Response

| Field | Type | Description |
|-------|------|-------------|
| `dataset_id` | int | Internal ID of the dataset |
| `task_pk` | int | Internal primary key of the task |
| `session_id` | int \| null | Internal ID of the newly stored session (`null` if `store=false`) |
| `session_number` | int \| null | Sequential session number within the task (`null` if `store=false`) |
| `digested_sessions` | int | Always `0` — digestion runs in the background |
| `status` | string | `"stored"` \| `"stored_and_digest_queued"` \| `"digest_queued"` |

**Example response:**

```json
{
  "dataset_id": 1,
  "task_pk": 1,
  "session_id": 3,
  "session_number": 3,
  "digested_sessions": 0,
  "status": "stored_and_digest_queued"
}
```

> **Note:** Digestion is asynchronous. The API returns immediately after queueing the background task. Memories will be searchable once the digest worker finishes processing.

---

## POST /api/memory/search

Queries the memory store for a given question, running up to six retrieval paths in parallel and returning a packed, token-budgeted context string ready for use in an LLM prompt.

### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `dataset` | string | yes | — | Must match the dataset used during ingestion |
| `task` | string | yes | — | Must match the task used during ingestion |
| `question` | string | yes | — | Natural-language question to search for |
| `mode` | string | no | `"expand"` | Retrieval mode: `"direct"`, `"expand"`, or `"reflect"` |
| `strategy` | string | no | `"rrf"` | Fusion strategy: `"rrf"` or `"rerank"` |
| `top_k` | int | no | `12` | Number of top facts to keep after fusion (configurable via `QA_RERANK_TOP_K`) |

### `mode` parameter

Controls how many retrieval paths are activated and whether LLM query rewriting is used.

| Mode | Paths | LLM calls | Use when |
|------|-------|-----------|----------|
| `direct` | 3 (A + B + C) | none | Fast lookups, no LLM service needed |
| `expand` | 6 (A + B + B2 + B3 + C + D) | query rewriting + expansion | Default — best balance of recall and speed |
| `reflect` | 6 + agentic round 2 | expansion + reflection | Hard multi-hop questions, maximum recall |

The six retrieval paths are:

| Path | Method | Description |
|------|--------|-------------|
| A | BM25 | Keyword search on facts using the (optionally rewritten) query |
| B | Embedding | Semantic similarity on facts using the original question |
| B2 | Embedding | Semantic similarity on facts using a HyDE hypothetical-document query |
| B3 | Embedding | Semantic similarity on facts using an event-focused query variant |
| C | Entity tree | Beam search through the hierarchical entity tree |
| D | Structured BM25 | Tantivy query with AND/OR semantics generated by the LLM |

#### `mode="direct"` — 3-path, no rewriting

Runs paths A, B, and C directly on the original question with no LLM calls. Fastest and cheapest.

```bash
curl -X POST "http://localhost:9574/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "question": "What does Okabe drink while debugging?",
    "mode": "direct"
  }'
```

#### `mode="expand"` — 6-path with LLM query expansion (default)

Rewrites the question and generates multiple query variants before retrieval, activating all six paths. Best balance of recall and latency.

```bash
curl -X POST "http://localhost:9574/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "question": "What is Okabe'\''s preferred drink while debugging, and what does he think it tastes like?",
    "mode": "expand"
  }'
```

#### `mode="reflect"` — 6-path + agentic round 2

After round-1 retrieval, an LLM evaluates whether the retrieved facts are sufficient to answer the question. If not, it generates 1–2 targeted follow-up queries and performs a second retrieval pass. Highest recall, highest cost.

```bash
curl -X POST "http://localhost:9574/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "question": "Has Amadeus ever expressed a genuine compliment to Okabe, and in what context?",
    "mode": "reflect"
  }'
```

### `strategy` parameter

Controls how candidate facts from multiple retrieval paths are merged into a single ranked list.

#### `strategy="rrf"` — Reciprocal Rank Fusion (default)

Combines ranked lists from all active paths using the RRF formula: each fact's score is the sum of `1 / (60 + rank)` across every path it appears in. No additional service calls required.

Best choice when a rerank service is unavailable or when latency matters.

```bash
curl -X POST "http://localhost:9574/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "question": "What is Okabe'\''s preferred drink while debugging, and what does he think it tastes like?",
    "strategy": "rrf"
  }'
```

#### `strategy="rerank"` — Cross-encoder reranking

Passes all candidate facts to a cross-encoder reranker (configured via `RERANK_SERVICE_URL`) for precise relevance scoring. Requires a running rerank service. Higher accuracy, higher latency.

```bash
curl -X POST "http://localhost:9574/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "question": "What is Okabe'\''s preferred drink while debugging, and what does he think it tastes like?",
    "strategy": "rerank"
  }'
```

### `top_k` parameter

Controls how many top-scored facts survive after fusion. Facts beyond `top_k` are discarded before token-budget packing. All surviving facts are included in `packed_context` subject to a ~4500-token budget.

```bash
# Retrieve more facts for a complex multi-hop question
curl -X POST "http://localhost:9574/api/memory/search" \
  -H "Content-Type: application/json" \
  -d '{
    "dataset": "lab_member_001_okabe",
    "task": "casual_chat_with_amadeus",
    "question": "Has Amadeus ever expressed a genuine compliment to Okabe, and in what context?",
    "top_k": 20
  }'
```

### Response

| Field | Type | Description |
|-------|------|-------------|
| `packed_context` | string | Ready-to-use context for LLM prompts: session summaries followed by a bullet-list of facts, within a token budget |
| `packed_token_count` | int | Estimated token count of `packed_context` |
| `fact_ids` | array[int] | IDs of facts included in `packed_context` (chronological order) |
| `facts` | array | Full details of all retrieved + fused facts, ordered by relevance score |
| `sessions` | array | Relevant session summaries contributing to the context |
| `raw_messages` | array | Always `[]` in the current version |

**Fact object fields (`facts[*]`):**

| Field | Type | Description |
|-------|------|-------------|
| `fact_id` | int | Unique fact identifier |
| `text` | string | Fact text with entity references resolved |
| `source` | string | Retrieval path that found this fact: `"bm25"`, `"embed"`, `"tree"`, or `"bm25_parsed"` |
| `rerank_score` | float | Relevance score after fusion or reranking |
| `time_info` | string | Resolved timestamp, e.g. `"2025-12-05"` |
| `entity_ref` | string | Canonical entity this fact belongs to, e.g. `"Okabe"` |
| `aspect_path` | string | Entity tree location, e.g. `"Habits > Beverages"` |

**Session object fields (`sessions[*]`):**

| Field | Type | Description |
|-------|------|-------------|
| `session_summary_id` | int | Unique session summary identifier |
| `session_id` | int | Parent session identifier |
| `subject` | string | One-line subject of the session |
| `content` | string | Full session summary text |
| `score` | float | Relevance score |
| `source` | string | How the session was retrieved: `"bm25"` or `"fact_agg"` |
| `contributing_facts` | int | Number of top facts whose session matches this one |

**Example response:**

```json
{
  "packed_context": "## Relevant Episodes\n\n**Debugging and beverages**: Okabe and Amadeus discussed his habit of drinking Dr. Pepper while debugging. Amadeus teased him while secretly approving.\n---\n\n## Additional Facts\n- Okabe drinks Dr. Pepper while debugging [2025-12-05]\n- Okabe thinks Dr. Pepper tastes like carbonated cough syrup [2025-12-05]",
  "packed_token_count": 92,
  "fact_ids": [1, 2],
  "facts": [
    {
      "fact_id": 1,
      "text": "Okabe drinks Dr. Pepper while debugging",
      "source": "bm25",
      "rerank_score": 0.032,
      "time_info": "2025-12-05",
      "entity_ref": "Okabe",
      "aspect_path": "Habits > Beverages"
    },
    {
      "fact_id": 2,
      "text": "Okabe thinks Dr. Pepper tastes like carbonated cough syrup",
      "source": "embed",
      "rerank_score": 0.028,
      "time_info": "2025-12-05",
      "entity_ref": "Okabe",
      "aspect_path": "Habits > Beverages"
    }
  ],
  "sessions": [
    {
      "session_summary_id": 1,
      "session_id": 1,
      "subject": "Debugging and beverages",
      "content": "Okabe and Amadeus discussed his habit of drinking Dr. Pepper while debugging. Amadeus teased him while secretly approving.",
      "score": 14.2,
      "source": "bm25",
      "contributing_facts": 2
    }
  ],
  "raw_messages": []
}
```

---

## Mode and Strategy Combinations

| `mode` | `strategy` | Use case |
|--------|-----------|----------|
| `direct` | `rrf` | Fast lookups, no LLM service required |
| `expand` | `rrf` | Default — good balance of recall and speed |
| `expand` | `rerank` | Higher precision when a rerank service is available |
| `reflect` | `rrf` | Maximum recall without a rerank service |
| `reflect` | `rerank` | Maximum recall and precision for hard multi-hop questions |

