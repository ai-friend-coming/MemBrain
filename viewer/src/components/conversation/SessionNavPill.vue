<script setup lang="ts">
import type { TaskDetail } from '@/types'
import { onClickOutside } from '@vueuse/core'
import { computed, ref } from 'vue'

const props = defineProps<{
  task: TaskDetail
  isActive?: boolean
  activeSessionId: number | null
}>()

const emit = defineEmits<{
  (e: 'switch-session', id: number): void
}>()

const isDropdownOpen = ref(false)
const sessionDropdownRef = ref<HTMLElement | null>(null)
onClickOutside(sessionDropdownRef, () => {
  isDropdownOpen.value = false
})

const activeSession = computed(() =>
  props.activeSessionId != null
    ? props.task.sessions.find(s => s.id === props.activeSessionId) ?? null
    : null,
)

const activeSessionIndex = computed(() =>
  props.activeSessionId != null
    ? props.task.sessions.findIndex(s => s.id === props.activeSessionId)
    : -1,
)

const hasPrevSession = computed(() => activeSessionIndex.value > 0)
const hasNextSession = computed(() =>
  activeSessionIndex.value > -1 && activeSessionIndex.value < props.task.sessions.length - 1,
)

function toggleDropdown(event: Event) {
  event.stopPropagation()
  isDropdownOpen.value = !isDropdownOpen.value
}

function switchSession(id: number) {
  emit('switch-session', id)
  isDropdownOpen.value = false
}

function goToPrev() {
  if (hasPrevSession.value)
    switchSession(props.task.sessions[activeSessionIndex.value - 1].id)
}

function goToNext() {
  if (hasNextSession.value)
    switchSession(props.task.sessions[activeSessionIndex.value + 1].id)
}
</script>

<template>
  <Teleport v-if="isActive && task.sessions.length > 0" to="#header-controls">
    <div class="session-nav-pill">
      <button class="nav-btn prev-btn" :disabled="!hasPrevSession" title="Previous Session" @click="goToPrev">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
      </button>
      <div ref="sessionDropdownRef" class="dropdown-container">
        <button class="dropdown-trigger" @click="toggleDropdown">
          <span class="dropdown-label">{{ activeSession ? `Session ${activeSession.session_number}` : 'Sessions' }}</span>
          <span class="dropdown-arrow" :class="{ open: isDropdownOpen }">▾</span>
        </button>
        <div class="dropdown-menu" :class="{ 'is-open': isDropdownOpen }" @click.stop>
          <button
            v-for="session in task.sessions"
            :key="session.id"
            class="dropdown-item"
            :class="{ 'is-active': session.id === activeSessionId }"
            @click="switchSession(session.id)"
          >
            Session {{ session.session_number }}
          </button>
        </div>
      </div>
      <button class="nav-btn next-btn" :disabled="!hasNextSession" title="Next Session" @click="goToNext">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
      </button>
    </div>
  </Teleport>
</template>

<style scoped>
.session-nav-pill {
  display: flex;
  align-items: center;
  background-color: var(--c-bg);
  border: 1px solid var(--c-border);
  border-radius: 9999px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.session-nav-pill:hover, .session-nav-pill:focus-within {
  border-color: var(--c-border-strong);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
}
.nav-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: var(--c-text-5);
  width: 2rem;
  height: 2rem;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
  padding: 0;
}
.nav-btn:hover:not(:disabled) { background-color: var(--c-bg-muted); color: var(--c-text); }
.nav-btn:active:not(:disabled) { transform: scale(0.82); transition-duration: 0.08s; }
.nav-btn:disabled { color: var(--c-border-strong); cursor: not-allowed; }
.prev-btn { border-top-left-radius: 9999px; border-bottom-left-radius: 9999px; }
.next-btn { border-top-right-radius: 9999px; border-bottom-right-radius: 9999px; }
.dropdown-container { position: relative; display: flex; align-items: center; }
.dropdown-trigger {
  background-color: transparent;
  border: none;
  border-left: 1px solid var(--c-border);
  border-right: 1px solid var(--c-border);
  color: var(--c-text-4);
  font-size: var(--text-ui);
  font-weight: var(--fw-medium);
  padding: 0 0.875rem;
  height: 2rem;
  border-radius: 0;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
  display: flex;
  align-items: center;
  gap: 0.375rem;
  min-width: 100px;
}
.dropdown-label { display: block; }
.dropdown-arrow {
  font-size: 0.7rem;
  color: var(--c-text-muted);
  display: inline-block;
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
.dropdown-arrow.open {
  transform: rotate(180deg);
}
.dropdown-trigger:hover { background-color: var(--c-bg-subtle); color: var(--c-text); }
.dropdown-menu {
  display: none;
  position: absolute;
  right: -0.25rem;
  top: calc(100% + 0.5rem);
  background-color: var(--c-bg);
  min-width: 160px;
  max-width: 280px;
  max-height: 350px;
  overflow-y: auto;
  box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.1);
  border-radius: 0.75rem;
  border: 1px solid var(--c-border);
  z-index: 100;
  padding: 0.375rem;
}
.dropdown-menu.is-open {
  display: flex;
  flex-direction: column;
  animation: dropdown-fade-in 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.dropdown-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.375rem 0.625rem;
  border: none;
  background: transparent;
  color: var(--c-text-4);
  font-size: var(--text-ui);
  font-weight: var(--fw-medium);
  border-radius: 0.375rem;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
  white-space: nowrap;
}
.dropdown-item:hover { background-color: var(--c-bg-muted); color: var(--c-text); }
.dropdown-item.is-active { background-color: var(--c-accent-bg); color: var(--c-accent-fg); font-weight: var(--fw-semibold); }
@keyframes dropdown-fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
