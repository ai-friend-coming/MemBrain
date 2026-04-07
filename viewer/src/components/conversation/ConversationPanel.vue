<script setup lang="ts">
import type { ChatSession, TaskDetail } from '@/types'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { Search } from 'lucide-vue-next'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MessageItem from './MessageItem.vue'
import MessageSearchDialog from './MessageSearchDialog.vue'
import SessionNavPill from './SessionNavPill.vue'

const props = defineProps<{ task: TaskDetail, isActive?: boolean }>()

const activeSessionId = ref<number | null>(null)
const isMounted = ref(false)
const isSearchOpen = ref(false)
onMounted(() => {
  isMounted.value = true
})

watch(
  () => props.task,
  (newTask) => {
    if (newTask?.sessions?.length > 0) {
      const stored = localStorage.getItem(`membrain_active_session_${newTask.id}`)
      if (stored) {
        const sid = Number.parseInt(stored)
        if (newTask.sessions.some(s => s.id === sid)) {
          activeSessionId.value = sid
          return
        }
      }
      activeSessionId.value = newTask.sessions[0].id
    }
    else {
      activeSessionId.value = null
    }
  },
  { immediate: true },
)

watch(activeSessionId, (newId) => {
  if (newId != null && props.task)
    localStorage.setItem(`membrain_active_session_${props.task.id}`, newId.toString())
})

const activeSession = computed<ChatSession | null>(() => {
  if (!activeSessionId.value || !props.task?.sessions)
    return null
  return props.task.sessions.find(s => s.id === activeSessionId.value) ?? null
})

const scrollContainerRef = ref<HTMLElement | null>(null)
const highlightedMessageId = ref<string | null>(null)
let highlightTimeout: ReturnType<typeof setTimeout> | null = null

const messageIndexMap = computed<Map<string, number>>(() => {
  const map = new Map<string, number>()
  if (!activeSession.value)
    return map
  activeSession.value.messages.forEach((m, i) => {
    map.set(m.dia_id ? m.dia_id.replace(':', '-') : `id-${m.id}`, i)
  })
  return map
})

const rowVirtualizer = useVirtualizer(computed(() => ({
  count: activeSession.value ? activeSession.value.messages.length : 0,
  getScrollElement: () => scrollContainerRef.value,
  estimateSize: () => 120,
  overscan: 5,
  paddingStart: 32,
  gap: 20,
})))

const virtualItems = computed(() => rowVirtualizer.value.getVirtualItems())
const totalSize = computed(() => rowVirtualizer.value.getTotalSize())

const speakerColorMap = computed<Map<string, number>>(() => {
  const map = new Map<string, number>()
  if (!activeSession.value)
    return map
  let nextIndex = 0
  for (const msg of activeSession.value.messages) {
    if (!map.has(msg.speaker)) {
      map.set(msg.speaker, nextIndex % 5)
      nextIndex++
    }
  }
  return map
})

function getSpeakerColorIndex(speaker: string): number {
  return speakerColorMap.value.get(speaker) ?? 0
}

function getMsg(index: number) {
  return activeSession.value!.messages[index]
}
function getMsgId(index: number): string {
  const m = getMsg(index)
  return m.dia_id ? m.dia_id.replace(':', '-') : `id-${m.id}`
}

function switchSession(sessionId: number) {
  activeSessionId.value = sessionId
  nextTick(() => {
    rowVirtualizer.value.scrollToOffset(0)
  })
}

async function scrollToEvidence(evidenceRef: string) {
  const safeId = evidenceRef.replace(':', '-')
  if (!props.task?.sessions)
    return

  const sessionOnlyMatch = evidenceRef.match(/^S(\d+)$/)
  let targetSessionId = null
  for (const session of props.task.sessions) {
    if (sessionOnlyMatch) {
      if (session.session_number === Number(sessionOnlyMatch[1])) {
        targetSessionId = session.id
        break
      }
    }
    else if (session.messages.some(m => (m.dia_id ? m.dia_id.replace(':', '-') === safeId : `id-${m.id}` === safeId))) {
      targetSessionId = session.id
      break
    }
  }

  if (targetSessionId && activeSessionId.value !== targetSessionId) {
    activeSessionId.value = targetSessionId
    await nextTick()
  }

  if (sessionOnlyMatch) {
    rowVirtualizer.value.scrollToOffset(0)
    return
  }

  const idx = messageIndexMap.value.get(safeId)
  if (idx != null) {
    rowVirtualizer.value.scrollToIndex(idx, { align: 'center' })
    if (highlightTimeout)
      clearTimeout(highlightTimeout)
    highlightedMessageId.value = safeId
    highlightTimeout = setTimeout(() => {
      highlightedMessageId.value = null
    }, 2000)
  }
}

defineExpose({ scrollToEvidence })
</script>

<template>
  <div class="conversation-container">
    <Teleport v-if="isActive && isMounted && task?.sessions?.length > 0" to="#header-controls">
      <button class="msg-search-btn" @click="isSearchOpen = true" title="Search Messages">
        <Search :size="16" />
      </button>
    </Teleport>
    <MessageSearchDialog
      v-model:isOpen="isSearchOpen"
      :task="task"
      @select-message="(sid, mid) => scrollToEvidence(mid)"
    />
    <SessionNavPill
      v-if="isMounted"
      :task="task"
      :is-active="isActive"
      :active-session-id="activeSessionId"
      @switch-session="switchSession"
    />
    <div class="main-column">
      <div ref="scrollContainerRef" class="conv-scroll">
        <div v-if="activeSession" :key="activeSession.id" class="session-block">
          <div class="session-divider">
            <span>Session {{ activeSession.session_number }}</span>
            <span v-if="activeSession.session_time" class="session-date">
              {{ activeSession.session_time.replace('T', ' ').replace(/\.\d+$/, '') }}
            </span>
            <span v-else-if="activeSession.session_time_raw" class="session-date">
              {{ activeSession.session_time_raw }}
            </span>
          </div>
          <div :style="{ height: `${totalSize}px`, position: 'relative' }">
            <div
              v-for="vRow in virtualItems"
              :key="vRow.key"
              :ref="(el) => rowVirtualizer.measureElement(el as Element)"
              :data-index="vRow.index"
              :style="{ position: 'absolute', top: 0, left: 0, width: '100%', transform: `translateY(${vRow.start}px)` }"
            >
              <MessageItem :message="getMsg(vRow.index)" :highlighted="highlightedMessageId === getMsgId(vRow.index)" :color-index="getSpeakerColorIndex(getMsg(vRow.index).speaker)" />
            </div>
          </div>
        </div>
        <div class="scroll-pad" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.conversation-container { display: flex; flex: 1; height: 100%; overflow: hidden; background-color: transparent; }
.main-column { flex: 1; display: flex; flex-direction: column; height: 100%; position: relative; background: var(--c-bg); overflow: hidden; }
.conv-scroll { flex: 1; overflow-y: auto; padding: 1.5rem 3rem 0; display: flex; flex-direction: column; gap: 1.5rem; }
.scroll-pad { height: 4rem; }
.session-divider {
  display: flex; align-items: center; justify-content: center; gap: 1rem;
  font-size: var(--text-xs); font-weight: var(--fw-medium); color: var(--c-text-muted);
  text-transform: uppercase; letter-spacing: var(--ls-wider); margin: 1.5rem 0 2rem 0;
}
.session-divider::before, .session-divider::after { content: ''; flex: 1; height: 1px; background: var(--c-bg-muted); }
.session-divider > span:first-child { background: var(--c-bg-subtle); padding: 0.25rem 0.875rem; border-radius: 9999px; border: 1px solid var(--c-border); }
.session-date { font-weight: var(--fw-medium); font-size: var(--text-xs); font-variant-numeric: tabular-nums; }
.msg-search-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--c-bg);
  border: 1px solid var(--c-border);
  color: var(--c-text-5);
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  cursor: pointer;
  transition: opacity 0.15s ease-out;
  box-shadow: 0 1px 2px rgba(0,0,0,0.02);
  margin-right: 0.5rem;
}
.msg-search-btn:hover {
  background-color: var(--c-bg-muted);
  color: var(--c-text);
  border-color: var(--c-border-strong);
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.msg-search-btn:active {
  transform: scale(0.92);
}
</style>
