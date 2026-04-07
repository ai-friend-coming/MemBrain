<script setup lang="ts">
import type { Dataset, Task, TaskDetail } from '@/types'
import { onMounted, ref, watch } from 'vue'
import ConversationPanel from '@/components/conversation/ConversationPanel.vue'
import MemoryPanel from '@/components/memory/MemoryPanel.vue'
import QAPanel from '@/components/QAPanel.vue'
import SidebarTree from '@/components/SidebarTree.vue'
import { getTask, listDatasets, listTasks } from '@/services/api'

const datasets = ref<Dataset[]>([])
const tasks = ref<Task[]>([])
const taskDetail = ref<TaskDetail | null>(null)

const activeDatasetId = ref<number | null>(null)
const activeTaskId = ref<number | null>(null)
const activeTab = ref<'messages' | 'memory'>('messages')
// MemoryGraph is lazily mounted on first visit to Memory tab, then kept alive
const memoryEverOpened = ref(false)

watch(activeTab, (tab) => {
  if (tab === 'memory')
    memoryEverOpened.value = true
  if (activeTaskId.value !== null)
    localStorage.setItem(`membrain_task_${activeTaskId.value}_tab`, tab)
})

const loadingDatasets = ref(false)
const loadingTasks = ref(false)
const loadingTask = ref(false)
const error = ref<string | null>(null)

onMounted(async () => {
  loadingDatasets.value = true
  try {
    datasets.value = await listDatasets()
    const storedDid = localStorage.getItem('membrain_active_dataset_id')
    const storedTid = localStorage.getItem('membrain_active_task_id')
    if (storedDid) {
      const did = Number.parseInt(storedDid)
      if (datasets.value.some(d => d.id === did)) {
        await selectDataset(did)
        if (storedTid) {
          const tid = Number.parseInt(storedTid)
          if (tasks.value.some(t => t.id === tid)) {
            await selectTask(tid)
          }
        }
      }
    }
  }
  catch (e: any) {
    error.value = e.message ?? 'Failed to load datasets'
  }
  finally {
    loadingDatasets.value = false
  }
})

async function selectDataset(id: number) {
  if (activeDatasetId.value === id)
    return
  activeDatasetId.value = id
  localStorage.setItem('membrain_active_dataset_id', id.toString())
  activeTaskId.value = null
  localStorage.removeItem('membrain_active_task_id')
  taskDetail.value = null
  tasks.value = []
  loadingTasks.value = true
  try {
    tasks.value = await listTasks(id)
  }
  catch (e: any) {
    error.value = e.message ?? 'Failed to load tasks'
  }
  finally {
    loadingTasks.value = false
  }
}

async function selectTask(id: number) {
  if (activeTaskId.value === id)
    return
  activeTaskId.value = id
  localStorage.setItem('membrain_active_task_id', id.toString())
  taskDetail.value = null
  memoryEverOpened.value = false
  const savedTab = localStorage.getItem(`membrain_task_${id}_tab`)
  activeTab.value = savedTab === 'memory' ? 'memory' : 'messages'
  if (activeTab.value === 'memory')
    memoryEverOpened.value = true
  loadingTask.value = true
  try {
    taskDetail.value = await getTask(id)
  }
  catch (e: any) {
    error.value = e.message ?? 'Failed to load task'
  }
  finally {
    loadingTask.value = false
  }
}

const convPanelRef = ref<InstanceType<typeof ConversationPanel> | null>(null)

function onScrollToEvidence(ev: string) {
  if (convPanelRef.value && convPanelRef.value.scrollToEvidence) {
    convPanelRef.value.scrollToEvidence(ev)
  }
}
</script>

<template>
  <div class="viewer-layout">
    <!-- Error banner -->
    <div v-if="error" class="error-banner">
      {{ error }}
      <button class="error-dismiss" @click="error = null">
        ✕
      </button>
    </div>

    <!-- Merged Sidebar Tree -->
    <SidebarTree
      :datasets="datasets"
      :active-dataset-id="activeDatasetId"
      :active-task-id="activeTaskId"
      :tasks-for-active-dataset="tasks"
      :loading-tasks="loadingTasks"
      @select-dataset="selectDataset"
      @select-task="selectTask"
    />

    <!-- Conversation panel -->
    <div class="conv-panel">
      <div v-if="loadingTask || loadingDatasets" class="center-msg">
        <div class="loading-indicator">
          <svg class="spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <circle cx="12" cy="12" r="10" stroke-opacity="0.15" />
            <path d="M12 2a10 10 0 0 1 10 10" />
          </svg>
          <span>Loading…</span>
        </div>
      </div>
      <div v-else-if="datasets.length === 0" class="center-msg">
        <div class="empty-state">
          <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" /><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          </svg>
          <p class="empty-title">
            No datasets imported
          </p>
          <p class="empty-desc">
            Import a dataset to start exploring conversations and memory structures.
          </p>
        </div>
      </div>
      <div v-else-if="!taskDetail && !activeDatasetId" class="center-msg">
        <div class="empty-state">
          <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="18" rx="1" /><path d="M13 7h8M13 12h8M13 17h5" />
          </svg>
          <p class="empty-title">
            Open a dataset
          </p>
          <p class="empty-desc">
            Expand a dataset in the sidebar to browse its tasks.
          </p>
        </div>
      </div>
      <div v-else-if="!taskDetail && tasks.length === 0 && !loadingTasks" class="center-msg">
        <div class="empty-state">
          <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" /><rect x="9" y="3" width="6" height="4" rx="1" /><line x1="9" y1="12" x2="15" y2="12" /><line x1="9" y1="16" x2="13" y2="16" />
          </svg>
          <p class="empty-title">
            No tasks in this dataset
          </p>
          <p class="empty-desc">
            This dataset has no tasks yet.
          </p>
        </div>
      </div>
      <div v-else-if="!taskDetail" class="center-msg">
        <div class="empty-state">
          <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <p class="empty-title">
            Select a task
          </p>
          <p class="empty-desc">
            Choose a task from the sidebar to view its conversation, memory graph, and QA pairs.
          </p>
        </div>
      </div>
      <div v-else class="task-view-container">
        <!-- Unified Task Header -->
        <div class="unified-task-header">
          <div class="header-left">
            <span class="conv-title">{{ taskDetail.task_id }}</span>
          </div>

          <div class="header-center">
            <div class="task-tabs">
              <button
                class="tab-btn"
                :class="{ active: activeTab === 'messages' }"
                @click="activeTab = 'messages'"
              >
                Messages
              </button>
              <button
                class="tab-btn"
                :class="{ active: activeTab === 'memory' }"
                @click="activeTab = 'memory'"
              >
                Memory
              </button>
            </div>
          </div>

          <div id="header-controls" class="header-right" />
        </div>

        <!-- View Content -->
        <div class="task-content">
          <ConversationPanel
            v-show="activeTab === 'messages'"
            ref="convPanelRef"
            :task="taskDetail"
            :is-active="activeTab === 'messages'"
          />
          <MemoryPanel
            v-if="memoryEverOpened"
            v-show="activeTab === 'memory'"
            :task-id="taskDetail.id"
            :is-active="activeTab === 'memory'"
          />
        </div>
      </div>
    </div>

    <!-- QA Panel -->
    <QAPanel
      v-if="taskDetail && taskDetail.qa_pairs?.length > 0"
      :qa-pairs="taskDetail.qa_pairs"
      @scroll-to-evidence="onScrollToEvidence"
    />
  </div>
</template>

<style scoped>
.viewer-layout {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
  position: relative;
  background-color: var(--c-bg-subtle);
}

.conv-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background-color: transparent;
  position: relative;
  z-index: 20;
}

.center-msg {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-text-muted);
  font-size: var(--text-base);
}

.error-banner {
  position: absolute;
  top: 0.75rem;
  left: 50%;
  transform: translateX(-50%);
  background: var(--c-destructive-bg);
  color: var(--c-destructive-text);
  border: 1px solid var(--c-destructive-border);
  border-radius: 0.5rem;
  padding: 0.5rem 1rem;
  font-size: var(--text-base);
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.error-dismiss {
  cursor: pointer;
  font-size: 0.75rem;
  opacity: 0.7;
}

.error-dismiss:hover { opacity: 1; }

.task-view-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  overflow: hidden;
}

.unified-task-header {
  height: 64px;
  padding: 0 1.5rem;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  flex-shrink: 0;
  position: relative;
  z-index: 20;
  box-sizing: border-box;
  background: var(--c-bg);
  border-bottom: 1px solid var(--c-border);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: nowrap;
  white-space: nowrap;
  flex: 1;
}

.conv-title {
  font-size: var(--text-title);
  font-weight: var(--fw-semibold);
  color: var(--c-text);
  letter-spacing: -0.01em;
}

.task-speakers {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: nowrap;
}

.speaker-tag {
  font-size: var(--text-sm);
  font-weight: var(--fw-semibold);
  color: var(--c-text-4);
  background: var(--c-bg-muted);
  padding: 0.25rem 0.5rem;
  border-radius: 0.375rem;
  border: 1px solid var(--c-border);
}

.header-center {
  display: flex;
  justify-content: center;
  align-items: center;
  flex: 1;
}

.header-right {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex: 1;
  padding-right: 0.75rem;
}

.task-tabs {
  display: flex;
  align-items: center;
  background-color: var(--c-bg-muted);
  padding: 0.25rem;
  border-radius: 9999px;
  border: 1px solid var(--c-border);
  box-shadow: inset 0 1px 2px rgba(0,0,0,0.02);
}

.tab-btn {
  background: transparent;
  border: none;
  font-size: var(--text-ui);
  font-weight: var(--fw-medium);
  color: var(--c-text-5);
  padding: 0.375rem 1.25rem;
  border-radius: 9999px;
  cursor: pointer;
  transition: background-color 0.2s cubic-bezier(0.16, 1, 0.3, 1),
              color 0.15s,
              box-shadow 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.tab-btn:hover:not(.active) {
  color: var(--c-text-3);
}

.tab-btn.active {
  background-color: var(--c-bg);
  color: var(--c-text);
  font-weight: var(--fw-semibold);
  box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
}

.task-content {
  flex: 1;
  overflow: hidden;
  position: relative;
}

/* ── Empty states & loading ──────────────────────────────────────────────── */
.loading-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  color: var(--c-text-muted);
  font-size: var(--text-base);
}

.spinner {
  width: 22px;
  height: 22px;
  color: var(--c-accent);
  animation: spin-dv 0.8s linear infinite;
}

@keyframes spin-dv {
  to { transform: rotate(360deg); }
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  text-align: center;
  max-width: 280px;
}

.empty-icon {
  width: 30px;
  height: 30px;
  color: var(--c-border-strong);
  margin-bottom: 6px;
}

.empty-title {
  font-size: var(--text-base);
  font-weight: var(--fw-medium);
  color: var(--c-text-5);
  margin: 0;
}

.empty-desc {
  font-size: var(--text-ui);
  color: var(--c-text-muted);
  margin: 0;
  line-height: 1.5;
}
</style>
