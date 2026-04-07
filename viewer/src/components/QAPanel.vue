<script setup lang="ts">
import type { DropdownOption } from '@/components/ui/dropdown-select/DropdownSelect.vue'
import type { QAPair } from '@/types'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { Search } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'
import DropdownSelect from '@/components/ui/dropdown-select/DropdownSelect.vue'
import { isRegexInvalid } from '@/lib/utils'

const props = defineProps<{
  qaPairs: QAPair[]
}>()
const emit = defineEmits<{
  (e: 'scrollToEvidence', ev: string): void
}>()
const isCollapsed = ref(localStorage.getItem('membrain_qa_panel_collapsed') === 'true')
watch(isCollapsed, (val) => {
  localStorage.setItem('membrain_qa_panel_collapsed', String(val))
})

function handleEvidenceClick(ev: string) {
  emit('scrollToEvidence', ev)
}

const selectedCategory = ref<string | null>(null)
const searchQuery = ref('')
const useRegex = ref(false)

const isInvalidRegex = computed((): boolean => isRegexInvalid(searchQuery.value, useRegex.value))

const uniqueCategories = computed(() => {
  const cats = props.qaPairs.map(qa => qa.category).filter((c): c is string => c !== null)
  return [...new Set(cats)].sort()
})

const categoryOptions = computed<DropdownOption[]>(() => [
  { value: null, label: 'All Categories' },
  ...uniqueCategories.value.map(c => ({ value: c, label: c })),
])

const filteredQaPairs = computed(() => {
  let pairs = props.qaPairs
  if (selectedCategory.value !== null) {
    pairs = pairs.filter(qa => qa.category === selectedCategory.value)
  }

  if (!searchQuery.value)
    return pairs

  if (useRegex.value && !isInvalidRegex.value) {
    const re = new RegExp(searchQuery.value, 'i')
    return pairs.filter((qa) => {
      if (re.test(qa.question))
        return true
      if (qa.answer && re.test(qa.answer))
        return true
      if (qa.reasoning && re.test(qa.reasoning))
        return true
      if (qa.options) {
        for (const val of Object.values(qa.options)) {
          if (typeof val === 'string' && re.test(val))
            return true
        }
      }
      return false
    })
  }
  else if (!useRegex.value) {
    const q = searchQuery.value.toLowerCase()
    return pairs.filter((qa) => {
      if (qa.question.toLowerCase().includes(q))
        return true
      if (qa.answer && qa.answer.toLowerCase().includes(q))
        return true
      if (qa.reasoning && qa.reasoning.toLowerCase().includes(q))
        return true
      if (qa.options) {
        for (const val of Object.values(qa.options)) {
          if (typeof val === 'string' && val.toLowerCase().includes(q))
            return true
        }
      }
      return false
    })
  }

  return pairs
})

// --- Virtual scrolling ---
const qaScrollRef = ref<HTMLElement | null>(null)

function formatEvidenceLabel(ev: string): string {
  return ev.replace(/^(S\d+):(\d+)$/, (_, s, p) => `${s}:${Number(p) + 1}`)
}

function getQA(index: number): QAPair {
  return filteredQaPairs.value[index]
}

const qaVirtualizer = useVirtualizer(computed(() => ({
  count: filteredQaPairs.value.length,
  getScrollElement: () => qaScrollRef.value,
  estimateSize: () => 260,
  overscan: 3,
  paddingStart: 16,
  paddingEnd: 16,
  gap: 16,
})))

const qaVirtualItems = computed(() => qaVirtualizer.value.getVirtualItems())
const qaTotalSize = computed(() => qaVirtualizer.value.getTotalSize())

watch([selectedCategory, searchQuery, useRegex], () => {
  qaVirtualizer.value.scrollToOffset(0)
})
</script>

<template>
  <div class="qa-panel" :class="{ 'is-collapsed': isCollapsed }">
    <div class="panel-header" :class="{ 'collapsed-header': isCollapsed }">
      <button class="collapse-btn" :title="isCollapsed ? 'Expand Q&A Panel' : 'Collapse Q&A Panel'" @click="isCollapsed = !isCollapsed">
        <svg v-if="isCollapsed" viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
        <svg v-else viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6" /></svg>
      </button>

      <div v-show="!isCollapsed" class="panel-title-group">
        <span class="panel-title">Q&amp;A</span>
        <span class="panel-count">{{ filteredQaPairs.length }}</span>
      </div>

      <DropdownSelect
        v-if="!isCollapsed && uniqueCategories.length > 0"
        v-model="selectedCategory"
        :options="categoryOptions"
        style="max-width: 10rem"
      />
    </div>

    <div v-show="!isCollapsed" class="filter-bar">
      <div class="search-wrapper">
        <Search :size="16" class="search-icon" />
        <input v-model="searchQuery" type="text" placeholder="Search Q&A..." class="search-input" :class="{ 'regex-error': useRegex && isInvalidRegex }">
        <button class="regex-btn" :class="{ active: useRegex }" title="Use Regular Expression" @click="useRegex = !useRegex">
          .*
        </button>
      </div>
    </div>

    <div v-show="!isCollapsed" ref="qaScrollRef" class="qa-scroll">
      <div v-if="filteredQaPairs.length === 0" class="empty-qa">
        <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" /></svg>
        <p>No QA pairs found</p>
      </div>

      <div v-else :style="{ height: `${qaTotalSize}px`, position: 'relative' }">
        <div
          v-for="vRow in qaVirtualItems"
          :key="vRow.key"
          :ref="(el) => qaVirtualizer.measureElement(el as Element)"
          :data-index="vRow.index"
          class="virtual-row"
          :style="{
            transform: `translateY(${vRow.start}px)`,
          }"
        >
          <div class="qa-card">
            <div class="qa-question-block">
              <span class="qa-indicator">Q</span>
              <div class="qa-question">
                {{ getQA(vRow.index).question }}
              </div>
            </div>

            <div v-if="getQA(vRow.index).options" class="qa-options">
              <div
                v-for="(text, label) in getQA(vRow.index).options"
                :key="label"
                class="qa-option" :class="[label === getQA(vRow.index).answer ? 'correct-option' : '']"
              >
                <span class="option-label">{{ label }}</span>
                <span class="option-text">{{ text }}</span>
              </div>
            </div>

            <div v-else class="qa-answer-block">
              <span class="qa-indicator answer-indicator">A</span>
              <div class="qa-answer">
                {{ getQA(vRow.index).answer }}
              </div>
            </div>

            <div v-if="getQA(vRow.index).reasoning" class="qa-reasoning-wrap">
              <div class="qa-reasoning">
                <div class="reasoning-header">
                  <svg viewBox="0 0 24 24" width="12" height="12" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" /></svg>
                  Reasoning
                </div>
                <div class="reasoning-body">
                  {{ getQA(vRow.index).reasoning }}
                </div>
              </div>
            </div>

            <div class="qa-meta">
              <div v-if="getQA(vRow.index).category" class="qa-category">
                {{ getQA(vRow.index).category }}
              </div>

              <div v-if="getQA(vRow.index).evidence && getQA(vRow.index).evidence.length > 0" class="qa-evidence">
                <button
                  v-for="(ev, idx) in getQA(vRow.index).evidence"
                  :key="idx"
                  class="evidence-pill"
                  title="View evidence"
                  @click="handleEvidenceClick(ev)"
                >
                  <svg viewBox="0 0 24 24" width="10" height="10" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
                  {{ formatEvidenceLabel(ev) }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qa-panel {
  width: 340px;
  background: var(--c-bg);
  border-left: 1px solid var(--c-border);
  display: flex;
  flex-direction: column;
  height: 100%;
  flex-shrink: 0;
  z-index: 10;
  overflow: hidden;
  transition: width 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.qa-panel.is-collapsed {
  width: 52px;
}

.panel-header {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 1.25rem;
  box-sizing: border-box;
  position: relative;
  z-index: 20;
  gap: 0.5rem;
}

.panel-header.collapsed-header {
  padding: 0;
  justify-content: center;
}

.collapse-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--c-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.375rem;
  border-radius: 0.375rem;
  transition: background-color 0.15s, color 0.15s;
}

.collapse-btn:hover {
  background: var(--c-bg-subtle);
  color: var(--c-text);
}

.collapse-btn:focus-visible {
  outline: 2px solid var(--c-accent);
  outline-offset: 2px;
}

.panel-title-group {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.panel-title {
  font-size: var(--text-title);
  font-weight: var(--fw-semibold);
  color: var(--c-text);
  letter-spacing: -0.01em;
}

.panel-count {
  font-size: var(--text-xs);
  font-weight: var(--fw-semibold);
  color: var(--c-text-5);
  background: var(--c-bg-muted);
  padding: 0.125rem 0.4rem;
  border-radius: 9999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.filter-bar {
  padding: 0 1.25rem 0.75rem 1.25rem;
  display: flex;
  justify-content: stretch;
  flex-shrink: 0;
}

.search-wrapper { display: flex; align-items: center; background: var(--c-bg); border: 1px solid var(--c-border); border-radius: 0.5rem; padding: 0 0.5rem; width: 100%; height: 2.125rem; transition: border-color 0.2s, box-shadow 0.2s; }
.search-wrapper:focus-within { border-color: var(--c-accent, #6366f1); box-shadow: 0 0 0 2px rgba(99,102,241,0.1); }
.search-icon { color: var(--c-text-muted); margin-right: 0.5rem; flex-shrink: 0; }
.search-input { flex: 1; min-width: 0; background: transparent; border: none; padding: 0.5rem 0; font-size: 0.875rem; color: var(--c-text-3); outline: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.search-input.regex-error { color: var(--c-destructive); }
.search-input::placeholder { color: var(--c-text-muted); }
.regex-btn { flex-shrink: 0; background: transparent; border: 1px solid transparent; border-radius: 0.25rem; color: var(--c-text-muted); font-family: monospace; font-size: 0.875rem; padding: 0.125rem 0.375rem; cursor: pointer; transition: background-color 0.15s, border-color 0.15s, color 0.15s; }
.regex-btn:hover { background: var(--c-border); color: var(--c-text-4); }
.regex-btn.active { background: var(--c-accent-bg, #eef2ff); color: var(--c-accent, #6366f1); border-color: var(--c-accent-border, #c7d2fe); }

.qa-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0.5rem 1rem 1.25rem;
}

.empty-qa {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--c-text-muted);
  margin-top: 4rem;
  gap: 0.75rem;
}

.empty-icon {
  width: 32px;
  height: 32px;
  opacity: 0.5;
}

.empty-qa p {
  font-size: var(--text-base);
  font-weight: var(--fw-medium);
}

.virtual-row {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
}

.qa-card {
  background: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: 0.5rem;
  padding: 1rem;
  font-size: var(--text-base);
  line-height: 1.5;
  transition: border-color 0.15s, box-shadow 0.15s;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  position: relative;
  overflow: hidden;
}

.qa-card:hover {
  border-color: var(--c-border-strong);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.qa-question-block, .qa-answer-block {
  display: flex;
  gap: 0.625rem;
  align-items: flex-start;
}

.qa-indicator {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 0.25rem;
  background: var(--c-bg-muted);
  color: var(--c-text-4);
  font-size: var(--text-xs);
  font-weight: var(--fw-semibold);
  flex-shrink: 0;
  margin-top: 0.125rem;
}

.answer-indicator {
  background: var(--c-border);
  color: var(--c-text-3);
}

.qa-question {
  color: var(--c-text);
  font-weight: var(--fw-medium);
  line-height: var(--lh-normal);
}

.qa-answer {
  color: var(--c-text-3);
  line-height: var(--lh-normal);
}

.qa-reasoning-wrap {
  padding-left: 1.875rem;
}

.qa-reasoning {
  background: var(--c-bg-subtle);
  border-radius: 0.5rem;
  border: 1px solid var(--c-border);
  overflow: hidden;
}

.reasoning-header {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.75rem;
  background: var(--c-bg-muted);
  color: var(--c-text-5);
  font-size: var(--text-xs);
  font-weight: var(--fw-semibold);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.reasoning-body {
  padding: 0.625rem 0.75rem;
  font-size: var(--text-ui);
  color: var(--c-text-4);
  line-height: 1.5;
  white-space: pre-wrap;
}

.qa-options {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  padding-left: 1.875rem;
}

.qa-option {
  display: flex;
  gap: 0.625rem;
  color: var(--c-text-4);
  line-height: 1.4;
  padding: 0.375rem 0.5rem;
  border-radius: 0.375rem;
  background: var(--c-bg-subtle);
  border: 1px solid transparent;
  transition: background-color 0.15s, border-color 0.15s, color 0.15s;
}

.qa-option.correct-option {
  background: #ecfdf5;
  color: var(--c-success);
  border-color: #a7f3d0;
  font-weight: var(--fw-medium);
}

.qa-option.correct-option .option-label {
  color: var(--c-success);
}

.option-label {
  flex-shrink: 0;
  font-weight: var(--fw-semibold);
  color: var(--c-text-5);
  min-width: 1.25rem;
}

.option-text {
  flex: 1;
}

.qa-meta {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-top: 0.125rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--c-bg-muted);
}

.qa-category {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  font-size: var(--text-xs);
  color: var(--c-text-5);
  background: var(--c-bg-muted);
  padding: 0.125rem 0.5rem;
  border-radius: 9999px;
  font-weight: var(--fw-semibold);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.qa-evidence {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.375rem;
}

.evidence-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border: 1px solid var(--c-accent-border);
  background: var(--c-accent-bg);
  color: var(--c-accent-fg);
  font-size: var(--text-xs);
  font-weight: var(--fw-medium);
  padding: 0.3rem 0.6rem;
  min-height: 28px;
  border-radius: 9999px;
  cursor: pointer;
  transition: background-color 0.15s, border-color 0.15s, transform 0.1s cubic-bezier(0.16, 1, 0.3, 1);
}

.evidence-pill:hover {
  background: #dde4ff;
  border-color: var(--c-accent);
}

.evidence-pill:focus-visible {
  outline: 2px solid var(--c-accent);
  outline-offset: 2px;
}

.evidence-pill:active {
  transform: scale(0.92);
  background: #c7d2fe;
}

/* ── Responsive ────────────────────────────────────────────────────────────── */
@media (max-width: 1280px) {
  .qa-panel:not(.is-collapsed) {
    width: 290px;
  }
}

@media (max-width: 1100px) {
  .qa-panel:not(.is-collapsed) {
    width: 260px;
  }
  .panel-header {
    height: 52px;
    padding: 0 0.75rem;
  }
}
</style>
