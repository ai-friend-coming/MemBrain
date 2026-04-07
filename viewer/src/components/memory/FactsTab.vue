<script setup lang="ts">
import type { FactPageItem, FactsPage } from '@/types'
import { ChevronLeft, ChevronRight, FileText } from 'lucide-vue-next'
import { onBeforeUnmount, ref } from 'vue'
import { formatFactText } from '@/lib/utils'

defineProps<{
  factsPage: FactsPage | null
  loading: boolean
  hasFacts: boolean
  totalPages: number
  currentPage: number
  cardHeight: number
}>()

const emit = defineEmits<{
  (e: 'update:currentPage', v: number): void
}>()

const factsViewRef = ref<HTMLElement | null>(null)
defineExpose({ factsViewRef })

// Custom Hover Tooltip Logic
const hoverFact = ref<FactPageItem | null>(null)
const hoverX = ref(0)
const hoverY = ref(0)
const hoverAlignY = ref<'top' | 'bottom'>('top')
let hoverTimer: ReturnType<typeof setTimeout> | null = null

function onFactEnter(e: MouseEvent, fact: FactPageItem) {
  if (hoverTimer)
    clearTimeout(hoverTimer)
  const target = e.currentTarget as HTMLElement
  const bodyEl = target.querySelector('.fact-body')
  if (!bodyEl)
    return

  // Only show tooltip if the text is genuinely truncated
  const isTruncated = bodyEl.scrollHeight > bodyEl.clientHeight + 1
  if (!isTruncated) {
    hoverFact.value = null
    return
  }

  hoverTimer = setTimeout(() => {
    const rect = target.getBoundingClientRect()
    hoverFact.value = fact

    // Position above or below depending on available space
    const isTopHalf = rect.top < window.innerHeight / 2
    hoverY.value = isTopHalf ? rect.bottom + 8 : rect.top - 8
    hoverX.value = rect.left + rect.width / 2
    hoverAlignY.value = isTopHalf ? 'top' : 'bottom'
  }, 1000) // 1 second delay avoids accidental triggering
}

function onFactLeave() {
  if (hoverTimer)
    clearTimeout(hoverTimer)
  hoverTimer = setTimeout(() => {
    hoverFact.value = null
  }, 100)
}

onBeforeUnmount(() => {
  if (hoverTimer)
    clearTimeout(hoverTimer)
})
</script>

<template>
  <div class="facts-tab">
    <div ref="factsViewRef" class="facts-content">
      <div v-if="(factsPage?.facts ?? []).length === 0" class="center-msg">
        <div class="empty-state">
          <FileText :size="30" class="empty-icon" />
          <p class="empty-desc">{{ hasFacts ? 'Try a different search term or clear the filter.' : 'Facts are extracted from conversations during ingestion.' }}</p>
        </div>
      </div>
      <div v-else class="facts-grid">
        <div
          v-for="(fact, index) in (factsPage?.facts ?? [])"
          :key="fact.id"
          class="fact-card"
          :style="{ height: `${cardHeight}px` }"
          @mouseenter="onFactEnter($event, fact)"
          @mouseleave="onFactLeave"
        >
          <div class="fact-body truncate-multiline">{{ formatFactText(fact.text) }}</div>
          <div class="fact-bottom">
            <span class="fact-id-badge">#{{ (factsPage?.offset ?? 0) + index + 1 }}</span>
            <span v-if="fact.batch_index != null" class="fact-batch-badge" title="Batch">B{{ fact.batch_index }}</span>
            <span v-if="fact.fact_ts" class="fact-ts-badge" :title="fact.fact_ts">{{ fact.fact_ts }}</span>
            <div class="fact-spacer"></div>
            <span v-if="fact.status" class="fact-status-badge" :class="fact.status.toLowerCase()">
              <span class="status-dot"></span>
              {{ fact.status }}
            </span>
          </div>
        </div>
      </div>
    </div>
    <div class="facts-pagination" :style="{ visibility: totalPages > 1 ? 'visible' : 'hidden' }">
      <button class="page-btn" :disabled="currentPage === 1" @click="emit('update:currentPage', currentPage - 1)">
        <ChevronLeft :size="16" />
      </button>
      <span class="page-info">Page {{ currentPage }} of {{ totalPages }}</span>
      <button class="page-btn" :disabled="currentPage >= totalPages" @click="emit('update:currentPage', currentPage + 1)">
        <ChevronRight :size="16" />
      </button>
    </div>

    <!-- Custom Hover Tooltip -->
    <Teleport to="body">
      <Transition name="tooltip-fade">
        <div
          v-if="hoverFact"
          class="custom-fact-tooltip"
          :style="{
            top: `${hoverY}px`,
            left: `${hoverX}px`,
            transform: hoverAlignY === 'top' ? 'translate(-50%, 0)' : 'translate(-50%, -100%)',
          }"
          @mouseenter="hoverTimer && clearTimeout(hoverTimer)"
          @mouseleave="onFactLeave"
        >
          {{ formatFactText(hoverFact?.text || '') }}
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.facts-tab { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: transparent; }
.facts-content { flex: 1; overflow: hidden; padding: 1.25rem 1.5rem; display: flex; flex-direction: column; }

/* Responsive grid: fills available width; card height is fixed by JS calculation */
.facts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 0.75rem;
  align-content: start;
  flex: 1;
}

.fact-card {
  box-sizing: border-box;
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: 0.5rem;
  padding: 0.625rem 1rem;
  transition: border-color 0.15s;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  /* height is set via inline style from JS; min-height guards against tiny values */
  min-height: 106px;
  overflow: hidden;
}
.fact-card:hover { border-color: var(--c-border-strong); }

/* fact-body must NOT use flex:1 — it prevents -webkit-line-clamp from firing */
.fact-body {
  font-size: var(--text-base);
  color: var(--c-text-3);
  line-height: var(--lh-normal);
  font-weight: var(--fw-medium);
  /* Reserve exactly 3 lines: 3 × font-size × line-height = 3 × 0.875rem × 1.5 */
  height: calc(3 * 0.875rem * 1.5);
  overflow: hidden;
}
.truncate-multiline {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  text-overflow: ellipsis;
  white-space: normal;
  word-break: break-word;
}
.fact-bottom {
  margin-top: auto;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: nowrap;
  min-width: 0;
}

.fact-spacer { flex: 1; min-width: 0.25rem; }

.fact-id-badge, .fact-batch-badge, .fact-ts-badge {
  font-size: var(--text-xs);
  font-weight: var(--fw-semibold);
  padding: 0.125rem 0.4rem;
  border-radius: 9999px;
  font-variant-numeric: tabular-nums;
  border: 1px solid transparent;
  white-space: nowrap;
  flex-shrink: 0;
}

.fact-id-badge { background: #f1f5f9; color: #64748b; border-color: #e2e8f0; }
.fact-batch-badge { background: #eff6ff; color: #3b82f6; border-color: #bfdbfe; }
.fact-ts-badge {
  background: #f0fdfa; color: #0d9488; border-color: #ccfbf1;
  flex-shrink: 1; /* allow shrinking if space is constrained */
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.fact-status-badge {
  font-size: var(--text-xs);
  font-weight: var(--fw-semibold);
  padding: 0.125rem 0.5rem;
  border-radius: 9999px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  text-transform: capitalize;
  border: 1px solid transparent;
  white-space: nowrap;
  flex-shrink: 0;
}

.fact-status-badge .status-dot { width: 6px; height: 6px; border-radius: 50%; }
.fact-status-badge.active { background: #ecfdf5; color: #10b981; border-color: #a7f3d0; }
.fact-status-badge.active .status-dot { background: #10b981; }
.fact-status-badge.invalidated { background: #fef2f2; color: #ef4444; border-color: #fecaca; }
.fact-status-badge.invalidated .status-dot { background: #ef4444; }

/* Custom Tooltip */
.custom-fact-tooltip {
  position: fixed;
  z-index: 100000;
  max-width: 360px;
  padding: 12px 16px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(10px);
  color: #334155;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  font-size: 0.8125rem;
  line-height: 1.5;
  pointer-events: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08), 0 1px 3px rgba(0, 0, 0, 0.04);
}

.tooltip-fade-enter-active,
.tooltip-fade-leave-active {
  transition: opacity 0.15s ease-out, transform 0.15s ease-out;
}
.tooltip-fade-enter-from,
.tooltip-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, calc(var(--y-dir, 0) + 4px));
}

.facts-pagination {
  display: flex; align-items: center; justify-content: center;
  gap: 1rem; padding: 0.75rem 1rem 1rem;
}
.page-btn {
  display: flex; align-items: center; justify-content: center;
  background: var(--c-bg); border: 1px solid var(--c-border);
  color: var(--c-text-4); width: 2rem; height: 2rem;
  border-radius: 0.5rem; cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s;
}
.page-btn:hover:not(:disabled) { background: var(--c-bg-subtle); border-color: var(--c-border-strong); }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.page-info { font-size: var(--text-base); color: var(--c-text-5); font-weight: var(--fw-medium); font-variant-numeric: tabular-nums; }

.center-msg { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--c-text-muted); }
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 6px; max-width: 280px; padding: 8px; }
.empty-icon { color: var(--c-border-strong); margin-bottom: 6px; }
.empty-desc { font-size: var(--text-base); color: var(--c-text-muted); margin: 0; text-align: center; line-height: var(--lh-normal); }
</style>
