<script setup lang="ts">
import type { TaskDetail } from '@/types'
import { onClickOutside, useEventListener } from '@vueuse/core'
import { MessageSquare, Search } from 'lucide-vue-next'
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps<{
  task: TaskDetail | null
  isOpen: boolean
}>()

const emit = defineEmits<{
  (e: 'update:isOpen', value: boolean): void
  (e: 'selectMessage', sessionId: number, evidenceRef: string): void
}>()

const query = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const dialogRef = ref<HTMLElement | null>(null)

onClickOutside(dialogRef, () => {
  if (props.isOpen)
    emit('update:isOpen', false)
})

watch(() => props.isOpen, (open) => {
  if (open) {
    query.value = ''
    nextTick(() => {
      searchInput.value?.focus()
    })
  }
})

useEventListener('keydown', (e) => {
  if (e.key === 'Escape' && props.isOpen) {
    emit('update:isOpen', false)
  }
})

// Search logic
const searchResults = computed(() => {
  if (!query.value.trim() || !props.task?.sessions)
    return []

  const q = query.value.toLowerCase()
  const results: { session: any, messages: any[] }[] = []

  for (const session of props.task.sessions) {
    const matchedMsgs = session.messages.filter(m => m.content && m.content.toLowerCase().includes(q))
    if (matchedMsgs.length > 0) {
      results.push({
        session,
        messages: matchedMsgs,
      })
    }
  }
  return results
})

function highlightMatch(text: string) {
  if (!query.value.trim())
    return text
  const q = query.value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`(${q})`, 'gi')
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped.replace(re, '<mark class="msg-search-hl">$1</mark>')
}

function selectMsg(sessionId: number, messageId: string) {
  emit('selectMessage', sessionId, messageId)
  emit('update:isOpen', false)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="isOpen" class="dialog-overlay">
        <div ref="dialogRef" class="dialog-content">
          <div class="search-header">
            <Search :size="18" class="search-icon" />
            <input
              ref="searchInput"
              v-model="query"
              placeholder="Search messages across all sessions..."
              class="search-input"
            />
            <div class="esc-hint">ESC</div>
          </div>

          <div class="search-body">
            <div v-if="!query.trim()" class="empty-state">
              <MessageSquare :size="32" class="empty-icon" />
              <p>Type to search through all messages</p>
            </div>
            <div v-else-if="searchResults.length === 0" class="empty-state">
              <p>No messages found matching "{{ query }}"</p>
            </div>
            <div v-else class="results-list">
              <template v-for="res in searchResults" :key="res.session.id">
                <div class="session-group-title">Session {{ res.session.session_number }}</div>
                <div
                  v-for="msg in res.messages"
                  :key="msg.id"
                  class="result-item"
                  @click="selectMsg(res.session.id, msg.dia_id ? msg.dia_id : `id-${msg.id}`)"
                >
                  <div class="msg-speaker">{{ msg.speaker }}</div>
                  <div class="msg-text" v-html="highlightMatch(msg.content)" />
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
.fade-enter-active .dialog-content {
  animation: slide-down 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.4);
  backdrop-filter: blur(2px);
  z-index: 1000;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 10vh;
}

.dialog-content {
  width: 100%;
  max-width: 600px;
  background: var(--c-bg);
  border-radius: 12px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  border: 1px solid var(--c-border);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  max-height: 75vh;
}

@keyframes slide-down {
  from { transform: translateY(-10px) scale(0.98); }
  to { transform: translateY(0) scale(1); }
}

.search-header {
  display: flex;
  align-items: center;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--c-border);
  gap: 0.75rem;
}

.search-icon {
  color: var(--c-text-muted);
}

.search-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 1rem;
  color: var(--c-text);
  font-family: inherit;
}

.search-input::placeholder {
  color: var(--c-text-muted);
}

.esc-hint {
  font-size: var(--text-xs);
  font-weight: var(--fw-semibold);
  color: var(--c-text-muted);
  background: var(--c-bg-muted);
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid var(--c-border);
  font-variant-numeric: tabular-nums;
}

.search-body {
  overflow-y: auto;
  flex: 1;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem 1rem;
  color: var(--c-text-muted);
  gap: 0.75rem;
  font-size: var(--text-base);
}

.empty-icon {
  color: var(--c-border-strong);
  opacity: 0.7;
}

.results-list {
  padding: 0.5rem;
}

.session-group-title {
  padding: 0.5rem 0.75rem;
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--c-text-muted);
  text-transform: uppercase;
  letter-spacing: var(--ls-wider);
  margin-top: 0.5rem;
}

.result-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.75rem;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.1s;
}

.result-item:hover {
  background-color: var(--c-bg-muted);
}

.msg-speaker {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--c-text-4);
}

.msg-text {
  font-size: var(--text-base);
  color: var(--c-text-2);
  line-height: var(--lh-snug);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

:deep(.msg-search-hl) {
  background: rgba(99, 102, 241, 0.2);
  color: inherit;
  border-radius: 2px;
}
</style>
