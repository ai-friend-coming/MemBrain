# Frontend Codebase Map

Stack: Vue 3 (Composition API + `<script setup>`), TypeScript, Vite (rolldown-vite), UnoCSS + Tailwind v4, shadcn/vue (radix-vue / reka-ui).

## File Map

```
viewer/src/
├── main.ts                     # createApp + mount, imports uno.css + style.css
├── App.vue                     # Root: wraps DatasetViewer + Toaster
├── constants.ts                # API_BASE_URL = '/api'
├── types.ts                    # All TS interfaces (Dataset, Task, TaskDetail, MemoryGraph, …)
├── style.css                   # CSS custom properties (--c-bg, --c-text, --text-base, …)
├── views/
│   └── DatasetViewer.vue       # Only "page". Holds all top-level state. No router.
├── components/
│   ├── conversation/
│   │   ├── ConversationPanel.vue  # Session state + virtualizer + scrollToEvidence
│   │   ├── SessionNavPill.vue    # Teleported session nav pill (prev/next/dropdown)
│   │   └── MessageItem.vue       # Single message card rendering
│   ├── memory/
│   │   ├── MemoryPanel.vue       # Orchestrator: run selector, tab routing, all state
│   │   ├── EntitiesTab.vue       # Full Entities tab: 3-mode toggle (tree/graph/overview), entity picker, sub-view routing
│   │   ├── EntityTree.vue        # Entity tree view: entity→treenode→fact, collapsible (presentational)
│   │   ├── EntityGlobalGraph.vue # Canvas global graph: all entities, force layout, zoom, entity-centering on select
│   │   ├── EntityLocalGraph.vue  # Canvas local tree: selected entity subtree, radial layout, always-on animation
│   │   ├── FactsTab.vue          # Facts grid + adaptive pagination + regex search (presentational)
│   │   └── SessionsTab.vue       # Session summaries + pagination + regex search (presentational)
│   ├── SidebarTree.vue         # Left sidebar: dataset/task tree
│   ├── QAPanel.vue             # Right panel: QA pairs (virtual scroll)
│   └── ui/                     # shadcn primitives — no business logic
│       ├── button/
│       ├── command/            # Combobox search (used in MemoryPanel for entity/batch select)
│       ├── popover/
│       └── sonner/             # Toast
├── services/
│   └── api.ts                  # All fetch calls (see API section below)
└── lib/
    └── utils.ts                # cn() (clsx+twMerge), isRegexInvalid()
```

## Layout

Three-column layout, no routing:

```
[ SidebarTree 250px ] [ DatasetViewer center (ConversationPanel | MemoryPanel) ] [ QAPanel ]
```

- `QAPanel` only renders when `taskDetail.qa_pairs.length > 0`
- `MemoryPanel` lazy-mounted on first Memory tab visit, then kept alive with `v-show`

## State

All state lives in `DatasetViewer.vue` (no Pinia/Vuex). Children receive props, emit events upward.
`MemoryPanel` holds all memory-related state internally (run selector, tab filters, pagination, entity tree).

Key localStorage keys:
- `membrain_active_dataset_id`, `membrain_active_task_id`
- `membrain_task_<id>_tab` — remembers 'messages' or 'memory'
- `membrain_active_session_<taskId>` — active session in ConversationPanel
- `membrain_memory_<taskId>` — MemoryPanel state (tab, search, page, entity, …)

## API Calls (`services/api.ts`)

| Function | Endpoint |
|---|---|
| `listDatasets()` | GET /datasets |
| `listTasks(datasetId)` | GET /datasets/:id/tasks |
| `getTask(taskId)` | GET /tasks/:id |
| `listTaskRuns(taskId)` | GET /tasks/:id/runs |
| `getTaskMemory(taskId, runTag)` | GET /tasks/:id/runs/:tag/memory |
| `getTaskFacts(taskId, runTag, limit, offset, batchIndex?, search?)` | GET /tasks/:id/runs/:tag/memory/facts |
| `getTaskSessionSummaries(taskId, runTag, limit, offset)` | GET /tasks/:id/runs/:tag/memory/summaries |

## Key Patterns

- **Teleport**: `MemoryPanel` teleports its run-selector into `#header-controls`; `ConversationPanel` teleports the session nav pill into `#header-controls`.
- **Virtual scroll**: `ConversationPanel` (estimateSize=120) and `QAPanel` (estimateSize=260) use `@tanstack/vue-virtual`.
- **Regex search**: `MemoryPanel` facts tab and sessions tab support keyword or regex search; validated by `isRegexInvalid()`.
- **Adaptive pagination**: Facts tab uses `ResizeObserver` to dynamically compute `itemsPerPage` based on container size (rows × columns grid).
- **Run selector**: `MemoryPanel` fetches available run tags via `listTaskRuns`, then loads data for selected `run_tag`.
- **EntitiesTab tree structure**: three node types — `entity` (top level), `treenode` (aspect/root), `fact` (leaf). Collapse state managed in `EntitiesTab` via `expandedNodes`. The 3 view modes are: `tree` (EntityTree), `graph` (EntityLocalGraph — canvas radial subtree), and `overview` (EntityGlobalGraph — canvas force-layout global graph).
