<script setup lang="ts">
import type { FactsPage, MemoryGraph, SessionSummariesPage } from '@/types'
import { Check, ChevronsUpDown, Focus, Globe, ListTree, RefreshCw, Search, Share2 } from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Button } from '@/components/ui/button'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import DropdownSelect from '@/components/ui/dropdown-select/DropdownSelect.vue'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { isRegexInvalid } from '@/lib/utils'
import { getTaskFacts, getTaskMemory, getTaskSessionSummaries, listTaskRuns } from '@/services/api'
import EntitiesTab from './EntitiesTab.vue'
import FactsTab from './FactsTab.vue'
import SessionsTab from './SessionsTab.vue'

const props = defineProps<{
  taskId: number
  isActive?: boolean
}>()

const loading = ref(false)
const error = ref<string | null>(null)
const memoryData = ref<MemoryGraph | null>(null)

const activeTab = ref<'facts' | 'entities' | 'sessions'>('facts')
const selectedEntityId = ref<string | null>(null)
const entityViewMode = ref<'tree' | 'graph' | 'overview'>('tree')

// Facts pagination state
const factsPage = ref<FactsPage | null>(null)
const searchQuery = ref('')
const useRegex = ref(false)
const selectedBatch = ref<number | null>(null)
const currentPage = ref(1)
const batchComboOpen = ref(false)

// Session summaries state
const ssPage = ref<SessionSummariesPage | null>(null)
const ssSearchQuery = ref('')
const ssCurrentPage = ref(1)
const ssItemsPerPage = ref(6)
const ssUseRegex = ref(false)

// Run selector state
const selectedRunTag = ref<string | null>(null)
const availableRuns = ref<string[]>([])
const runOptions = computed(() => availableRuns.value.map(r => ({ value: r as string | null, label: r })))

// Entity filtering state
const entityComboOpen = ref(false)
const uniqueEntities = computed(() => memoryData.value?.entities ?? [])
const entityOptions = computed(() =>
  uniqueEntities.value.map(e => ({
    label: e.canonical_ref,
    value: e.entity_id,
    sublabel: e.aliases.length > 0 ? e.aliases.join(', ') : undefined,
  })),
)
const selectedEntityLabel = computed(() => {
  const opt = entityOptions.value.find(o => o.value === selectedEntityId.value)
  return opt?.label ?? null
})

// ── State persistence ────────────────────────────────────────────────────────
const storageKey = computed(() => `membrain_memory_${props.taskId}`)
let isRestoringState = false

function saveState() {
  if (isRestoringState)
    return
  try {
    localStorage.setItem(storageKey.value, JSON.stringify({
      tab: activeTab.value,
      entityId: selectedEntityId.value,
      entityViewMode: entityViewMode.value,
      search: searchQuery.value,
      regex: useRegex.value,
      batch: selectedBatch.value,
      page: currentPage.value,
      ssSearch: ssSearchQuery.value,
      ssRegex: ssUseRegex.value,
      ssPage: ssCurrentPage.value,
    }))
  }
  catch {}
}

function restoreState(data: MemoryGraph) {
  const raw = localStorage.getItem(storageKey.value)
  if (!raw)
    return
  try {
    const s = JSON.parse(raw)
    isRestoringState = true
    if (s.tab === 'facts' || s.tab === 'entities' || s.tab === 'sessions')
      activeTab.value = s.tab
    if (s.entityViewMode === 'constellation')
      entityViewMode.value = 'overview'
    else if (s.entityViewMode === 'tree' || s.entityViewMode === 'graph' || s.entityViewMode === 'overview')
      entityViewMode.value = s.entityViewMode
    if (s.search != null)
      searchQuery.value = s.search
    if (typeof s.regex === 'boolean')
      useRegex.value = s.regex
    if (s.batch !== undefined)
      selectedBatch.value = s.batch
    if (s.entityId && data.entities.some(e => e.entity_id === s.entityId))
      selectedEntityId.value = s.entityId
    if (s.page >= 1)
      currentPage.value = s.page
    if (s.ssSearch != null)
      ssSearchQuery.value = s.ssSearch
    if (typeof s.ssRegex === 'boolean')
      ssUseRegex.value = s.ssRegex
    if (s.ssPage >= 1)
      ssCurrentPage.value = s.ssPage
    nextTick(() => {
      isRestoringState = false
    })
  }
  catch {
    isRestoringState = false
  }
}
// ────────────────────────────────────────────────────────────────────────────

const isMounted = ref(false)

function selectBatch(idx: number | null) {
  selectedBatch.value = idx
}

const itemsPerPage = ref(9)
const factCardHeight = ref(106)
const factsTabRef = ref<InstanceType<typeof FactsTab> | null>(null)
let resizeObserver: ResizeObserver | null = null
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null
// Prevents watch(itemsPerPage) from re-fetching during initial load
let suppressItemsPerPageWatch = false

const isInvalidRegex = computed((): boolean => isRegexInvalid(searchQuery.value, useRegex.value))

const batchOptions = computed(() => factsPage.value?.batch_options ?? [])

const totalPages = computed(() =>
  Math.max(1, Math.ceil((factsPage.value?.total ?? 0) / itemsPerPage.value)),
)

// Session summary computed properties
const ssIsInvalidRegex = computed((): boolean => isRegexInvalid(ssSearchQuery.value, ssUseRegex.value))

const hasSessions = computed(() => (ssPage.value?.total ?? 0) > 0)

const ssTotalPages = computed(() =>
  Math.max(1, Math.ceil((ssPage.value?.total ?? 0) / ssItemsPerPage.value)),
)

watch([ssSearchQuery, ssUseRegex], () => {
  if (isRestoringState)
    return
  ssCurrentPage.value = 1
  if (searchDebounceTimer)
    clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => fetchSessions(), 300)
})

watch([searchQuery, useRegex], () => {
  if (isRestoringState)
    return
  currentPage.value = 1
  if (searchDebounceTimer)
    clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => fetchFacts(), 300)
})

watch(selectedBatch, () => {
  if (!isRestoringState) {
    currentPage.value = 1
    fetchFacts()
  }
})

watch(currentPage, () => {
  if (!isRestoringState)
    fetchFacts()
})

watch(ssCurrentPage, () => {
  if (!isRestoringState)
    fetchSessions()
})

watch([activeTab, selectedEntityId, entityViewMode, searchQuery, useRegex, selectedBatch, currentPage, ssSearchQuery, ssUseRegex, ssCurrentPage], saveState)

watch(itemsPerPage, () => {
  if (suppressItemsPerPageWatch)
    return
  currentPage.value = 1
  fetchFacts()
})

watch(ssTotalPages, (newTotal) => {
  if (ssCurrentPage.value > newTotal && newTotal > 0)
    ssCurrentPage.value = 1
})

watch(totalPages, (newTotal) => {
  if (currentPage.value > newTotal && newTotal > 0)
    currentPage.value = 1
})

function calculateItemsPerPage() {
  const el = factsTabRef.value?.factsViewRef
  if (!el)
    return
  const h = el.clientHeight
  const w = el.clientWidth
  if (h <= 0 || w <= 0)
    return

  // Vertical: padding 1.25rem×2 = 40px, row-gap 12px
  // Actual card min-height = top/bottom padding(20) + body(63) + gap(6) + badges(17) = 106px
  const availH = h - 40
  const rows = Math.max(1, Math.floor((availH + 12) / (106 + 12)))
  // Derive exact card height so a full page fills the container perfectly
  const cardH = Math.floor((availH - (rows - 1) * 12) / rows)
  // Horizontal: padding 1.5rem×2 = 48px, card min-width 340px, col-gap 12px
  const cols = Math.max(1, Math.floor((w - 48 + 12) / (340 + 12)))
  const items = rows * cols

  if (itemsPerPage.value !== items && items > 0)
    itemsPerPage.value = items
  if (factCardHeight.value !== cardH && cardH >= 106)
    factCardHeight.value = cardH
}

async function fetchFacts() {
  if (!selectedRunTag.value)
    return
  try {
    const offset = (currentPage.value - 1) * itemsPerPage.value
    const page = await getTaskFacts(
      props.taskId,
      selectedRunTag.value,
      itemsPerPage.value,
      offset,
      selectedBatch.value,
      searchQuery.value || null,
    )
    factsPage.value = page
    // Sync page if backend clamped the offset
    if (offset !== page.offset)
      currentPage.value = Math.floor(page.offset / itemsPerPage.value) + 1
  }
  catch {}
}

async function fetchSessions() {
  if (!selectedRunTag.value)
    return
  try {
    const offset = (ssCurrentPage.value - 1) * ssItemsPerPage.value
    const page = await getTaskSessionSummaries(
      props.taskId,
      selectedRunTag.value,
      ssItemsPerPage.value,
      offset,
      ssSearchQuery.value || null,
    )
    ssPage.value = page
    if (offset !== page.offset)
      ssCurrentPage.value = Math.floor(page.offset / ssItemsPerPage.value) + 1
  }
  catch {}
}

// Load Data
async function loadData() {
  await fetchRuns()
  if (!selectedRunTag.value)
    return
  loading.value = true
  suppressItemsPerPageWatch = true
  error.value = null
  try {
    const data = await getTaskMemory(props.taskId, selectedRunTag.value!)
    memoryData.value = data
    if (data.entities.length > 0)
      selectedEntityId.value = data.entities[0].entity_id
    restoreState(data)
  }
  catch (err: any) {
    error.value = err.message || 'Failed to fetch memory data'
  }
  finally {
    loading.value = false
    // Wait for FactsTab to render, then measure — itemsPerPage watch is still suppressed
    await nextTick()
    if (activeTab.value === 'facts') {
      calculateItemsPerPage()
      const el = factsTabRef.value?.factsViewRef
      if (el && resizeObserver) {
        resizeObserver.disconnect()
        resizeObserver.observe(el)
      }
    }
    // Single fetch with the correct limit; watch stays suppressed during the await
    await Promise.all([fetchFacts(), fetchSessions()])
    suppressItemsPerPageWatch = false
  }
}

watch(activeTab, (newTab) => {
  if (newTab === 'facts') {
    nextTick(() => {
      calculateItemsPerPage()
      const el = factsTabRef.value?.factsViewRef
      if (el && resizeObserver)
        resizeObserver.observe(el)
    })
  }
})

const hasFacts = computed(() => (factsPage.value?.total ?? 0) > 0)

onMounted(() => {
  isMounted.value = true
  fetchRuns().then(() => {
    if (selectedRunTag.value)
      loadData()
  })

  resizeObserver = new ResizeObserver(() => {
    if (activeTab.value === 'facts') {
      calculateItemsPerPage()
    }
  })
})

onBeforeUnmount(() => {
  if (resizeObserver)
    resizeObserver.disconnect()
})

watch(() => props.taskId, () => {
  selectedRunTag.value = null
  fetchRuns().then(() => {
    if (selectedRunTag.value)
      loadData()
  })
})

async function fetchRuns() {
  try {
    const runs = await listTaskRuns(props.taskId)
    availableRuns.value = runs.map(r => r.run_tag)
    if (!selectedRunTag.value || !availableRuns.value.includes(selectedRunTag.value))
      selectedRunTag.value = availableRuns.value[0] ?? null
  }
  catch {
    availableRuns.value = []
  }
}

function onRunSelect(val: string | null) {
  if (!val || val === selectedRunTag.value)
    return
  selectedRunTag.value = val
  loadData()
}
</script>

<template>
  <div class="memory-container">
    <div v-if="loading" class="center-msg">
      <div class="loading-indicator">
        <svg class="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="12" cy="12" r="10" stroke-opacity="0.15" />
          <path d="M12 2a10 10 0 0 1 10 10" />
        </svg>
        <span>Loading memory graph…</span>
      </div>
    </div>
    <div v-else-if="error" class="center-msg">
      <div class="error-state">
        <span class="error-text">{{ error }}</span>
        <button class="error-dismiss" @click="error = null">
          ✕
        </button>
      </div>
    </div>
    <div v-else-if="!memoryData" class="center-msg">
      <div class="empty-state">
        <Share2 :size="30" class="empty-icon" />
        <p class="empty-desc">
          Run the ingestion pipeline to extract facts and entity structures from this task's conversations.
        </p>
      </div>
    </div>
    <div v-else class="memory-layout">
      <Teleport v-if="isMounted && isActive && availableRuns.length > 0" to="#header-controls">
        <DropdownSelect :model-value="selectedRunTag" :options="runOptions" @update:model-value="onRunSelect" />
      </Teleport>

      <div class="unified-filter-bar">
        <div class="sub-tabs">
          <button class="subtab-btn" :class="{ active: activeTab === 'facts' }" title="Extracted atomic statements from conversations" @click="activeTab = 'facts'">
            Facts
          </button>
          <button class="subtab-btn" :class="{ active: activeTab === 'entities' }" title="Entity knowledge graph with hierarchical fact structure" @click="activeTab = 'entities'">
            Entities
          </button>
          <button class="subtab-btn" :class="{ active: activeTab === 'sessions' }" title="AI-generated summaries of each conversation session" @click="activeTab = 'sessions'">
            Summaries
          </button>
        </div>

        <div v-if="activeTab === 'entities'" class="center-view-toggle">
          <div class="view-mode-toggle">
            <button class="view-mode-btn" :class="{ active: entityViewMode === 'tree' }" title="Hierarchy View" @click="entityViewMode = 'tree'">
              <ListTree :size="16" />
            </button>
            <button class="view-mode-btn" :class="{ active: entityViewMode === 'graph' }" title="Local Focus" @click="entityViewMode = 'graph'">
              <Focus :size="16" />
            </button>
            <button class="view-mode-btn" :class="{ active: entityViewMode === 'overview' }" title="Global Overview" @click="entityViewMode = 'overview'">
              <Globe :size="16" />
            </button>
          </div>
        </div>

        <template v-if="activeTab === 'facts'">
          <div class="filter-controls">
            <Popover v-if="batchOptions.length > 0" v-model:open="batchComboOpen">
              <PopoverTrigger as-child>
                <Button variant="outline" class="h-[2.125rem] rounded-full px-4 text-sm font-medium gap-1.5">
                  {{ selectedBatch !== null ? `Batch ${selectedBatch}` : 'All batches' }}
                  <ChevronsUpDown class="opacity-50" :size="14" />
                </Button>
              </PopoverTrigger>
              <PopoverContent class="w-44 p-1" align="start">
                <button class="dropdown-item" :class="{ 'is-active': selectedBatch === null }" @click="selectBatch(null); batchComboOpen = false">
                  All batches
                </button>
                <button v-for="idx in batchOptions" :key="idx" class="dropdown-item" :class="{ 'is-active': selectedBatch === idx }" @click="selectBatch(idx); batchComboOpen = false">
                  Batch {{ idx }}
                </button>
              </PopoverContent>
            </Popover>
            <div class="search-wrapper">
              <Search :size="16" class="search-icon" />
              <input v-model="searchQuery" type="text" placeholder="Search facts..." class="search-input" :class="{ 'regex-error': useRegex && isInvalidRegex }">
              <button class="regex-btn" :class="{ active: useRegex }" title="Use Regular Expression" @click="useRegex = !useRegex">
                .*
              </button>
            </div>
            <div class="filter-stats">
              {{ factsPage?.total ?? 0 }} fact{{ (factsPage?.total ?? 0) !== 1 ? 's' : '' }}
            </div>
            <button class="refresh-action-btn" :disabled="loading" title="Refresh Facts" @click="loadData">
              <RefreshCw :size="16" class="refresh-icon" :class="{ 'spin-anim': loading }" />
            </button>
          </div>
        </template>
        <template v-else-if="activeTab === 'sessions'">
          <div class="filter-controls">
            <div class="search-wrapper">
              <Search :size="16" class="search-icon" />
              <input v-model="ssSearchQuery" type="text" placeholder="Search summaries..." class="search-input" :class="{ 'regex-error': ssUseRegex && ssIsInvalidRegex }">
              <button class="regex-btn" :class="{ active: ssUseRegex }" title="Use Regular Expression" @click="ssUseRegex = !ssUseRegex">
                .*
              </button>
            </div>
            <div class="filter-stats">
              {{ ssPage?.total ?? 0 }} summar{{ (ssPage?.total ?? 0) !== 1 ? 'ies' : 'y' }}
            </div>
            <button class="refresh-action-btn" :disabled="loading" title="Refresh Summaries" @click="loadData">
              <RefreshCw :size="16" class="refresh-icon" :class="{ 'spin-anim': loading }" />
            </button>
          </div>
        </template>
        <template v-else-if="activeTab === 'entities'">
          <div class="filter-controls">
            <Popover v-model:open="entityComboOpen">
              <PopoverTrigger as-child>
                <div class="search-wrapper search-wrapper--trigger" role="combobox" :aria-expanded="entityComboOpen" tabindex="0">
                  <Search :size="16" class="search-icon" />
                  <span class="search-input" :class="{ placeholder: !selectedEntityLabel }">{{ selectedEntityLabel ?? 'Search entities...' }}</span>
                  <ChevronsUpDown :size="14" class="search-icon" />
                </div>
              </PopoverTrigger>
              <PopoverContent class="w-80 p-0" align="end">
                <Command>
                  <CommandInput placeholder="Search entities..." />
                  <CommandList>
                    <CommandEmpty>No matches found</CommandEmpty>
                    <CommandGroup>
                      <CommandItem
                        v-for="opt in entityOptions"
                        :key="opt.value"
                        :value="String(opt.value)"
                        @select="() => { selectedEntityId = opt.value; entityComboOpen = false }"
                      >
                        <Check :class="selectedEntityId === opt.value ? 'opacity-100' : 'opacity-0'" class="shrink-0" />
                        <div class="flex flex-col min-w-0">
                          <span>{{ opt.label }}</span>
                          <span v-if="opt.sublabel" class="text-xs text-muted-foreground truncate">{{ opt.sublabel }}</span>
                        </div>
                      </CommandItem>
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
            <div class="filter-stats">
              {{ entityOptions.length }} entity{{ entityOptions.length !== 1 ? 'ies' : '' }}
            </div>
            <button class="refresh-action-btn" :disabled="loading" title="Refresh Entities" @click="loadData">
              <RefreshCw :size="16" class="refresh-icon" :class="{ 'spin-anim': loading }" />
            </button>
          </div>
        </template>
      </div>

      <FactsTab
        v-if="activeTab === 'facts'"
        ref="factsTabRef"
        :facts-page="factsPage"
        :loading="loading"
        :has-facts="hasFacts"
        :total-pages="totalPages"
        :current-page="currentPage"
        :card-height="factCardHeight"
        @update:current-page="currentPage = $event"
      />
      <EntitiesTab
        v-else-if="activeTab === 'entities'"
        :data="memoryData"
        :selected-entity-id="selectedEntityId"
        :entity-view-mode="entityViewMode"
        :loading="loading"
        @update:selected-entity-id="selectedEntityId = $event"
        @update:entity-view-mode="entityViewMode = $event"
        @refresh="loadData"
      />
      <SessionsTab
        v-if="activeTab === 'sessions'"
        :sessions="ssPage?.summaries ?? []"
        :has-sessions="hasSessions"
        :ss-current-page="ssCurrentPage"
        :ss-total-pages="ssTotalPages"
        :search-query="ssSearchQuery"
        :use-regex="ssUseRegex"
        @update:ss-current-page="ssCurrentPage = $event"
      />
    </div>
  </div>
</template>

<style scoped>
.memory-container { width: 100%; height: 100%; display: flex; flex-direction: column; background-color: transparent; }
.center-msg { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--c-text-muted); font-size: var(--text-base); }
.error-state { display: flex; align-items: center; gap: 0.75rem; background: var(--c-destructive-bg); color: var(--c-destructive-text); border: 1px solid var(--c-destructive-border); border-radius: 0.5rem; padding: 0.5rem 1rem; font-size: var(--text-base); }
.error-dismiss { background: transparent; border: none; cursor: pointer; font-size: 0.75rem; color: var(--c-destructive-text); opacity: 0.7; padding: 0; line-height: 1; }
.error-dismiss:hover { opacity: 1; }
.memory-layout { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: var(--c-bg-subtle); }
.unified-filter-bar { position: relative; display: flex; justify-content: space-between; align-items: center; padding: 0.5rem 1rem 0.5rem 1.25rem; background: var(--c-bg); border-bottom: 1px solid var(--c-border); box-shadow: 0 1px 2px rgba(0,0,0,0.02); z-index: 10; flex-shrink: 0; }
.sub-tabs { display: flex; align-items: center; gap: 0.25rem; flex-wrap: nowrap; }
.subtab-btn { background: transparent; border: none; font-size: 0.8125rem; font-weight: 600; color: #64748b; padding: 0.25rem 0.5rem; border-radius: 9999px; cursor: pointer; white-space: nowrap; transition: background-color 0.2s cubic-bezier(0.16, 1, 0.3, 1), color 0.15s; }
.subtab-btn:hover { background: #f1f5f9; color: #0f172a; }
.subtab-btn:focus-visible { outline: 2px solid var(--c-accent); outline-offset: 2px; }
.subtab-btn.active { background: var(--c-accent, #6366f1); color: #ffffff; }

.center-view-toggle { position: absolute; left: 50%; transform: translateX(-50%); display: flex; z-index: 20; }
.view-mode-toggle { display: flex; border: 1px solid #e2e8f0; border-radius: 0.5rem; overflow: hidden; background: white; }
.view-mode-btn { display: flex; align-items: center; justify-content: center; background: transparent; border: none; padding: 0.375rem 0.625rem; color: #94a3b8; cursor: pointer; transition: background-color 0.15s, color 0.15s; }
.view-mode-btn:hover { background: #f1f5f9; color: #475569; }
.view-mode-btn:focus-visible { outline: 2px solid var(--c-accent); outline-offset: -2px; }
.view-mode-btn.active { background: var(--c-accent, #6366f1); color: #ffffff; }
.view-mode-btn + .view-mode-btn { border-left: 1px solid #e2e8f0; }

.filter-controls { display: flex; align-items: center; gap: 0.75rem; min-width: 22rem; justify-content: flex-end; }
.refresh-action-btn { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 0.5rem; color: #64748b; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: background-color 0.15s, border-color 0.15s, color 0.15s; box-shadow: 0 1px 2px rgba(0,0,0,0.02); }
.refresh-action-btn:hover:not(:disabled) { background: #f8fafc; color: #3b82f6; border-color: #cbd5e1; }
.refresh-action-btn:focus-visible { outline: 2px solid var(--c-accent); outline-offset: 2px; }
.refresh-action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.spin-anim { animation: spin 1s linear infinite; }
@keyframes spin { 100% { transform: rotate(360deg); } }
.search-wrapper { display: flex; align-items: center; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 0 0.5rem; width: 240px; height: 2.125rem; transition: border-color 0.2s, box-shadow 0.2s; }
.search-wrapper:focus-within { border-color: var(--c-accent, #6366f1); box-shadow: 0 0 0 2px rgba(99,102,241,0.1); }
.search-icon { color: #94a3b8; margin-right: 0.5rem; }
.search-input { flex: 1; background: transparent; border: none; padding: 0.5rem 0; font-size: 0.875rem; color: #334155; outline: none; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.search-input.regex-error { color: #ef4444; }
.search-input.placeholder { color: #94a3b8; }
.search-wrapper--trigger { cursor: pointer; user-select: none; }
.search-wrapper--trigger:hover { border-color: #cbd5e1; }
.search-wrapper--trigger:focus { outline: none; border-color: var(--c-accent, #6366f1); box-shadow: 0 0 0 2px rgba(99,102,241,0.1); }
.regex-btn { background: transparent; border: 1px solid transparent; border-radius: 0.25rem; color: #94a3b8; font-family: monospace; font-size: 0.875rem; padding: 0.125rem 0.375rem; cursor: pointer; transition: background-color 0.15s, border-color 0.15s, color 0.15s; }
.regex-btn:hover { background: #e2e8f0; color: #475569; }
.regex-btn:focus-visible { outline: 2px solid var(--c-accent); outline-offset: 1px; }
.regex-btn.active { background: var(--c-accent-bg, #eef2ff); color: var(--c-accent, #6366f1); border-color: var(--c-accent-border, #c7d2fe); }
.filter-stats { font-size: 0.8125rem; color: #64748b; font-weight: 500; min-width: 5.5rem; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; flex-shrink: 0; }
.dropdown-item { display: block; width: 100%; text-align: left; padding: 0.375rem 0.625rem; border: none; background: transparent; color: var(--c-text-4); font-size: var(--text-ui); font-weight: var(--fw-medium); border-radius: 0.375rem; cursor: pointer; transition: background-color 0.15s, color 0.15s; font-family: inherit; white-space: nowrap; }
.dropdown-item:hover { background-color: var(--c-bg-muted); color: var(--c-text); }
.dropdown-item.is-active { background-color: var(--c-accent-bg); color: var(--c-accent-fg); font-weight: var(--fw-semibold); }
.loading-indicator { display: flex; flex-direction: column; align-items: center; gap: 10px; color: #94a3b8; font-size: 0.875rem; }
.spinner { width: 22px; height: 22px; color: #6366f1; animation: spin-mg 0.8s linear infinite; }
@keyframes spin-mg { to { transform: rotate(360deg); } }
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 5px; max-width: 280px; padding: 8px; }
.empty-icon { width: 30px; height: 30px; color: #cbd5e1; margin-bottom: 6px; }
.empty-desc { font-size: 0.875rem; color: #94a3b8; margin: 0; text-align: center; line-height: 1.5; }
</style>
