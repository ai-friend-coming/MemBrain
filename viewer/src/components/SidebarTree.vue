<script setup lang="ts">
import type { Dataset, Task } from '@/types'
import { ChevronRight, PanelLeft, PanelLeftClose } from 'lucide-vue-next'
import { ref, watch } from 'vue'

const props = defineProps<{
  datasets: Dataset[]
  activeDatasetId: number | null
  activeTaskId: number | null
  tasksForActiveDataset: Task[] // Tasks loaded for the currently expanded dataset
  loadingTasks: boolean
}>()

const emit = defineEmits<{
  (e: 'selectDataset', id: number): void
  (e: 'selectTask', id: number): void
}>()

const isCollapsed = ref(false)

// Keep track of which dataset is expanded in the tree
const expandedDatasetId = ref<number | null>(null)

// If parent sets an active dataset (e.g. on load), expand it
watch(() => props.activeDatasetId, (newId) => {
  if (newId !== null) {
    expandedDatasetId.value = newId
  }
}, { immediate: true })

function toggleDataset(dsId: number) {
  if (expandedDatasetId.value === dsId) {
    // collapse
    expandedDatasetId.value = null
  }
  else {
    // expand and emit to load tasks
    expandedDatasetId.value = dsId
    emit('selectDataset', dsId)
  }
}

function handleTaskClick(taskId: number) {
  emit('selectTask', taskId)
}
</script>

<template>
  <div class="sidebar-tree" :class="{ 'is-collapsed': isCollapsed }">
    <div v-if="!isCollapsed" class="sidebar-header">
      <button class="collapse-btn" :title="isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'" @click="isCollapsed = !isCollapsed">
        <PanelLeft v-if="isCollapsed" class="icon-toggle" />
        <PanelLeftClose v-else class="icon-toggle" />
      </button>
    </div>
    <div v-else class="sidebar-header collapsed">
      <button class="collapse-btn" :title="isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'" @click="isCollapsed = !isCollapsed">
        <PanelLeft v-if="isCollapsed" class="icon-toggle" />
        <PanelLeftClose v-else class="icon-toggle" />
      </button>
    </div>
    <Transition name="tree-fade">
      <div v-if="!isCollapsed" class="tree-container">
        <div v-for="ds in datasets" :key="ds.id" class="tree-node">
          <!-- Dataset Row -->
          <div
            class="dataset-row"
            :class="{ expanded: expandedDatasetId === ds.id, active: activeDatasetId === ds.id && activeTaskId === null }"
            @click="toggleDataset(ds.id)"
          >
            <ChevronRight class="chevron-icon" :class="{ 'is-expanded': expandedDatasetId === ds.id }" />
            <div class="dataset-info">
              <span class="dataset-name">{{ ds.name }}</span>
              <span class="dataset-count">{{ ds.task_count }}</span>
            </div>
          </div>

          <!-- Tasks List (Only show if this dataset is expanded) -->
          <Transition name="tasks-slide">
            <div v-if="expandedDatasetId === ds.id" class="tasks-outer">
              <div class="tasks-container">
                <div v-if="loadingTasks" class="loading-tasks">
                  Loading tasks...
                </div>
                <div v-else-if="tasksForActiveDataset.length === 0" class="empty-tasks">
                  No tasks
                </div>
                <div
                  v-for="task in tasksForActiveDataset"
                  v-else
                  :key="task.id"
                  class="task-row"
                  :class="{ active: activeTaskId === task.id }"
                  @click="handleTaskClick(task.id)"
                >
                  <div class="task-circle" />
                  <div class="task-info">
                    <span class="task-sample">{{ task.task_id }}</span>
                  </div>
                </div>
              </div>
            </div>
          </Transition>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.sidebar-tree {
  width: 250px;
  background: var(--c-bg);
  border-right: 1px solid var(--c-border);
  box-shadow: 4px 0 24px rgba(0, 0, 0, 0.02);
  display: flex;
  flex-direction: column;
  height: 100%;
  flex-shrink: 0;
  transition: width 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.sidebar-tree.is-collapsed {
  width: 48px;
}

.sidebar-header {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 0 0.75rem;
  box-sizing: border-box;
  border-bottom: 1px solid rgba(15, 23, 42, 0.04);
}

.sidebar-header.collapsed {
  justify-content: center;
  padding: 0;
  border-bottom: 1px solid rgba(15, 23, 42, 0.04);
}

.collapse-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--c-text-muted);
  padding: 0.375rem;
  border-radius: 0.375rem;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.15s, color 0.15s;
}

.collapse-btn:hover {
  background-color: var(--c-bg-muted);
  color: var(--c-text-4);
}

.collapse-btn:focus-visible {
  outline: 2px solid var(--c-accent);
  outline-offset: 2px;
}

.icon-toggle {
  width: 1.25rem;
  height: 1.25rem;
}

.tree-container {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
}

/* Dataset Row */
.dataset-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  cursor: pointer;
  transition: background-color 0.15s;
  user-select: none;
}

.dataset-row:hover {
  background-color: var(--c-bg-muted);
}

.dataset-row:focus-visible {
  outline: 2px solid var(--c-accent);
  outline-offset: -2px;
}

.dataset-row.active {
  background-color: var(--c-accent-bg);
}

.chevron-icon {
  width: 1rem;
  height: 1rem;
  color: var(--c-text-muted);
  flex-shrink: 0;
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), color 0.15s;
}

.chevron-icon.is-expanded {
  transform: rotate(90deg);
}

.dataset-info {
  display: flex;
  flex: 1;
  justify-content: space-between;
  align-items: center;
  overflow: hidden;
}

.dataset-name {
  font-size: var(--text-base);
  font-weight: var(--fw-semibold);
  color: var(--c-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dataset-count {
  font-size: var(--text-xs);
  background: var(--c-border);
  color: var(--c-text-5);
  padding: 0.125rem 0.375rem;
  border-radius: 9999px;
  font-weight: var(--fw-semibold);
}

/* Tasks */
/* Outer wrapper: grid-based height animation (no layout property animation) */
.tasks-outer {
  display: grid;
  grid-template-rows: 1fr;
  overflow: hidden;
}

/* Grid child must have min-height: 0 so the row can collapse to 0fr */
.tasks-outer > .tasks-container {
  min-height: 0;
}

/* Enter: 0fr→1fr, exit: 1fr→0fr; exit is ~30% faster than enter */
.tasks-slide-enter-active {
  transition: grid-template-rows 0.22s cubic-bezier(0.16, 1, 0.3, 1),
              opacity 0.18s ease;
}
.tasks-slide-leave-active {
  transition: grid-template-rows 0.16s ease,
              opacity 0.12s ease;
}
.tasks-slide-enter-from,
.tasks-slide-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
}

.tasks-container {
  padding-left: 1.75rem;
  margin-top: 0.125rem;
  margin-bottom: 0.5rem;
}

.loading-tasks, .empty-tasks {
  font-size: var(--text-sm);
  color: var(--c-text-muted);
  padding: 0.25rem 0.5rem;
}

.task-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.5rem;
  border-radius: 0.375rem;
  cursor: pointer;
  transition: background-color 0.15s;
  margin-bottom: 0.125rem;
}

.task-row:hover {
  background-color: var(--c-bg-muted);
}

.task-row:focus-visible {
  outline: 2px solid var(--c-accent);
  outline-offset: -2px;
}

.task-row.active {
  background-color: var(--c-accent-bg);
  box-shadow: inset 2px 0 0 0 var(--c-accent);
}

.task-circle {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background-color: var(--c-border);
  flex-shrink: 0;
}

.task-row.active .task-circle {
  background-color: var(--c-accent);
}

.task-info {
  display: flex;
  align-items: center;
  overflow: hidden;
  width: 100%;
}

.task-sample {
  font-size: var(--text-ui);
  font-weight: var(--fw-medium);
  color: var(--c-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── Responsive ────────────────────────────────────────────────────────────── */
@media (max-width: 1100px) {
  .sidebar-tree:not(.is-collapsed) {
    width: 220px;
  }
}

/* ── Sidebar content transition ─────────────────────────────────────────── */
.tree-fade-enter-active {
  transition: opacity 0.18s cubic-bezier(0.16, 1, 0.3, 1);
  transition-delay: 0.12s; /* wait for width animation to open first */
}
.tree-fade-leave-active {
  transition: opacity 0.1s ease;
}
.tree-fade-enter-from,
.tree-fade-leave-to {
  opacity: 0;
}
</style>
