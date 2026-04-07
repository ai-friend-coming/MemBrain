<script setup lang="ts">
import type { SessionSummaryItem } from '@/types'
import { ChevronLeft, ChevronRight, MessageSquare } from 'lucide-vue-next'
import { computed } from 'vue'

const props = defineProps<{
  sessions: SessionSummaryItem[]
  hasSessions: boolean
  ssCurrentPage: number
  ssTotalPages: number
  searchQuery: string
  useRegex: boolean
}>()

const emit = defineEmits<{
  (e: 'update:ssCurrentPage', v: number): void
}>()

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const searchRe = computed((): RegExp | null => {
  if (!props.searchQuery)
    return null
  try {
    const pattern = props.useRegex
      ? props.searchQuery
      : props.searchQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    return new RegExp(`(${pattern})`, 'gi')
  }
  catch { return null }
})

function highlight(text: string): string {
  const escaped = escapeHtml(text)
  const re = searchRe.value
  if (!re)
    return escaped
  const htmlRe = new RegExp(
    re.source.replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c] || c),
    re.flags,
  )
  return escaped.replace(htmlRe, '<mark class="search-hl">$1</mark>')
}
</script>

<template>
  <div class="sessions-tab">
    <div class="sessions-content">
      <div v-if="sessions.length === 0" class="center-msg">
        <div class="empty-state">
          <MessageSquare :size="30" class="empty-icon" />
          <p class="empty-desc">{{ hasSessions ? 'Try a different search term or clear the filter.' : 'Summaries are generated during ingestion with the session-summarizer module.' }}</p>
        </div>
      </div>
      <div v-else class="sessions-list">
        <div v-for="s in sessions" :key="s.session_number" class="session-card">
          <div class="session-header">
            <span class="session-number-badge">S{{ s.session_number }}</span>
            <span class="session-subject" v-html="highlight(s.subject)"></span>
          </div>
          <div class="session-content" v-html="highlight(s.content)"></div>
        </div>
      </div>
    </div>
    <div v-if="ssTotalPages > 1" class="facts-pagination">
      <button class="page-btn" :disabled="ssCurrentPage <= 1" @click="emit('update:ssCurrentPage', ssCurrentPage - 1)">
        <ChevronLeft :size="16" />
      </button>
      <span class="page-info">Page {{ ssCurrentPage }} of {{ ssTotalPages }}</span>
      <button class="page-btn" :disabled="ssCurrentPage >= ssTotalPages" @click="emit('update:ssCurrentPage', ssCurrentPage + 1)">
        <ChevronRight :size="16" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.sessions-tab { flex: 1; display: flex; flex-direction: column; overflow: hidden; background: transparent; }
.sessions-content { flex: 1; overflow-y: auto; padding: 1.5rem; display: flex; flex-direction: column; }
.sessions-list { display: flex; flex-direction: column; gap: 1rem; flex: 1; }
.session-card { background: var(--c-bg); border: 1px solid var(--c-border); border-radius: 0.75rem; padding: 0.75rem 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
.session-header { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; }
.session-number-badge { font-size: var(--text-xs); font-weight: var(--fw-semibold); padding: 2px 7px; background: #ede9fe; border: 1px solid #c4b5fd; border-radius: 4px; white-space: nowrap; flex-shrink: 0; color: #6d28d9; }
.session-subject { font-size: var(--text-ui); font-weight: var(--fw-semibold); line-height: 1.4; color: var(--c-text); }
.session-content { font-size: var(--text-sm); line-height: 1.7; color: var(--c-text-2); white-space: pre-wrap; }
.facts-pagination { display: flex; align-items: center; justify-content: center; gap: 1rem; padding: 0.75rem 1rem 1rem; }
.page-btn { display: flex; align-items: center; justify-content: center; background: var(--c-bg); border: 1px solid var(--c-border); color: var(--c-text-4); width: 2rem; height: 2rem; border-radius: 0.5rem; cursor: pointer; transition: background-color 0.15s, border-color 0.15s; }
.page-btn:hover:not(:disabled) { background: var(--c-bg-subtle); border-color: var(--c-border-strong); }
.page-btn:disabled { opacity: 0.5; cursor: not-allowed; background: transparent; }
.page-info { font-size: var(--text-base); color: var(--c-text-5); font-weight: var(--fw-medium); }
.center-msg { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--c-text-muted); }
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 5px; max-width: 280px; padding: 8px; }
.empty-icon { width: 30px; height: 30px; color: var(--c-border-strong); margin-bottom: 6px; }
.empty-desc { font-size: var(--text-base); color: var(--c-text-muted); margin: 0; text-align: center; line-height: var(--lh-normal); }
:deep(.search-hl) { background: rgba(99, 102, 241, 0.2); color: inherit; border-radius: 2px; }
</style>
