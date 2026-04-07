<script setup lang="ts">
import { ChevronRight, Folder } from 'lucide-vue-next'

defineProps<{
  flatTreeData: any[]
}>()

const emit = defineEmits<{
  (e: 'toggle-expand', node: any): void
}>()
</script>

<template>
  <div class="entities-view folder-tree-viewport custom-scroll">
    <div class="folder-tree-container">
      <div
        v-for="node in flatTreeData"
        :key="node.id"
        class="tree-node-row"
        :class="{ 'is-clickable': node.hasChildren && node.type !== 'entity' }"
        :style="{ paddingLeft: `${node.depth * 28 + 24}px` }"
        @click="emit('toggleExpand', node)"
      >
        <div class="chevron-wrapper" :class="{ invisible: !node.hasChildren || node.type === 'entity' }">
          <ChevronRight :size="16" class="chevron-icon" :class="{ expanded: node.isExpanded }" />
        </div>
        <div v-if="node.type === 'entity'" class="node-content entity-node">
          <div class="entity-header-row">
            <span class="node-label node-label--entity">{{ node.name }}</span>
            <div class="entity-right-meta">
              <span v-if="node.desc" class="entity-meta-badge entity-desc-badge">
                <span class="meta-label">description</span><span class="meta-value">{{ node.desc }}</span>
              </span>
              <span v-if="(node.aliases || []).length > 0" class="entity-meta-badge entity-alias-badge">
                <span class="meta-label">aliases</span><span class="meta-value">{{ (node.aliases || []).join(', ') }}</span>
              </span>
            </div>
          </div>
          <p v-if="node.treeRootSummary" class="entity-root-summary">{{ node.treeRootSummary }}</p>
        </div>
        <div v-else-if="node.type === 'treenode'" class="node-content folder-node">
          <Folder :size="18" class="node-icon node-icon--folder" />
          <span class="node-label node-label--folder">{{ node.name }}</span>
        </div>
        <div v-else-if="node.type === 'fact'" class="node-content fact-item-node">
          <div class="fact-tree-card">
            <div v-if="node.prefixLabel" class="fact-prefix">{{ node.prefixLabel }}</div>
            <div class="fact-body">{{ node.name }}</div>
            <div class="fact-bottom">
              <span v-if="node.factData" class="fact-id-badge">#{{ node.factData.id }}</span>
              <span v-if="node.factData?.batch_index != null" class="fact-batch-badge" title="Batch">B{{ node.factData.batch_index }}</span>
              <span v-if="node.factData?.fact_ts" class="fact-ts-badge" :title="node.factData.fact_ts">{{ node.factData.fact_ts }}</span>
              <span v-if="node.factData?.status" class="fact-status-badge" :class="node.factData.status.toLowerCase()">
                <span class="status-dot"></span>{{ node.factData.status }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.entities-view { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.folder-tree-viewport { flex: 1; overflow-y: auto; overflow-x: auto; background: var(--c-bg-subtle); padding: 1.5rem 0; }
.folder-tree-container { min-width: 600px; display: flex; flex-direction: column; gap: 8px; }
.tree-node-row {
  display: flex; align-items: flex-start; position: relative; transition: background-color 0.2s;
  padding: 6px 2rem 6px 1.5rem; border-radius: 0.375rem; margin: 0 0.5rem;
}
.tree-node-row.is-clickable { cursor: pointer; }
.tree-node-row.is-clickable:hover { background-color: rgba(0,0,0,0.03); }
.chevron-wrapper { display: flex; align-items: center; justify-content: center; width: 20px; height: 24px; margin-right: 4px; flex-shrink: 0; color: var(--c-text-muted); transition: color 0.2s; }
.tree-node-row.is-clickable:hover .chevron-wrapper { color: var(--c-text-4); }
.chevron-wrapper.invisible { visibility: hidden; }
.chevron-icon { transition: transform 0.2s ease; }
.chevron-icon.expanded { transform: rotate(90deg); }
.node-content { display: flex; align-items: flex-start; position: relative; flex: 1; }
.node-icon { margin-right: 8px; flex-shrink: 0; margin-top: 2px; }
.node-label { font-size: var(--text-base); line-height: 1.5; margin-top: 1px; }
.node-label--entity { font-weight: var(--fw-semibold); }
.node-label--folder { color: var(--c-text-3); font-weight: var(--fw-medium); }
.node-icon--folder { color: var(--c-text-5); }
.entity-right-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 0.375rem; margin-left: 0.75rem; }
.entity-meta-badge { display: inline-flex; align-items: center; gap: 0.25rem; border-radius: 9999px; font-size: var(--text-xs); border: 1px solid var(--c-border); background: var(--c-bg-subtle); padding: 0.125rem 0.5rem; white-space: nowrap; }
.entity-meta-badge .meta-label { font-size: var(--text-xs); font-weight: var(--fw-semibold); text-transform: uppercase; letter-spacing: 0.04em; color: var(--c-text-muted); }
.entity-meta-badge .meta-value { font-weight: var(--fw-medium); color: var(--c-text-3); }
.entity-node { color: var(--c-text); flex-direction: column; align-items: flex-start; }
.entity-header-row { display: flex; align-items: center; flex-wrap: wrap; gap: 0.5rem; }
.entity-root-summary { margin: 0.375rem 0 0 0; font-size: 0.8125rem; color: var(--c-text-4); line-height: 1.6; font-style: italic; max-width: 700px; }
.folder-node { color: var(--c-text-3); align-items: flex-start; }
.fact-item-node { flex: 1; position: relative; }
.fact-tree-card { position: relative; max-width: 600px; display: flex; flex-direction: column; gap: 0.375rem; padding: 0.375rem 0 0.375rem 0.875rem; border-left: 2px solid #e2e8f0; margin-left: 0.5rem; transition: border-color 0.2s; }
.fact-tree-card:hover { border-left-color: #cbd5e1; }
.fact-prefix { font-size: var(--text-sm); font-weight: var(--fw-semibold); color: var(--c-text-muted); text-transform: uppercase; letter-spacing: var(--ls-wide); }
.fact-body { font-size: var(--text-base); color: var(--c-text-4); line-height: var(--lh-relaxed); font-weight: var(--fw-normal); }
.fact-bottom { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.125rem; flex-wrap: wrap; }

.fact-id-badge, .fact-batch-badge, .fact-ts-badge { font-size: var(--text-xs); font-weight: var(--fw-semibold); padding: 0.125rem 0.4rem; border-radius: 9999px; font-variant-numeric: tabular-nums; border: 1px solid transparent; }
.fact-id-badge { background: var(--c-bg-muted); color: var(--c-text-5); border-color: var(--c-border); }
.fact-batch-badge { background: #eff6ff; color: #3b82f6; border-color: #bfdbfe; }
.fact-ts-badge { background: #f0fdfa; color: #0d9488; border-color: #ccfbf1; }

.fact-status-badge { font-size: var(--text-xs); font-weight: var(--fw-semibold); padding: 0.125rem 0.5rem; border-radius: 9999px; display: inline-flex; align-items: center; gap: 4px; text-transform: capitalize; border: 1px solid transparent; }
.fact-status-badge .status-dot { width: 6px; height: 6px; border-radius: 50%; }
.fact-status-badge.active { background: #ecfdf5; color: #10b981; border-color: #a7f3d0; }
.fact-status-badge.active .status-dot { background: #10b981; }
.fact-status-badge.invalidated { background: #fef2f2; color: #ef4444; border-color: #fecaca; }
.fact-status-badge.invalidated .status-dot { background: #ef4444; }

.custom-scroll::-webkit-scrollbar { height: 8px; width: 8px; }
.custom-scroll::-webkit-scrollbar-track { background: transparent; }
.custom-scroll::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 9999px; border: 2px solid #fafafa; }
.custom-scroll::-webkit-scrollbar-thumb:hover { background-color: #94a3b8; }
</style>
