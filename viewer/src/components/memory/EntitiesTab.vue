<script setup lang="ts">
import type { FactPageItem, MemoryGraph } from '@/types'
import { Focus, Globe, ListTree } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { formatFactText } from '@/lib/utils'
import EntityGlobalGraph from './EntityGlobalGraph.vue'
import EntityLocalGraph from './EntityLocalGraph.vue'
import EntityTree from './EntityTree.vue'

const props = defineProps<{
  data: MemoryGraph
  selectedEntityId: string | null
  entityViewMode: 'tree' | 'graph' | 'overview'
  loading: boolean
}>()

const emit = defineEmits<{
  'update:selectedEntityId': [id: string]
  'update:entityViewMode': [mode: 'tree' | 'graph' | 'overview']
  'refresh': []
}>()

const expandedNodes = ref<Set<string>>(new Set())
const uniqueEntities = computed(() => props.data.entities)

const sortedTreeRoot = computed(() => {
  if (!props.selectedEntityId)
    return null
  const entity = uniqueEntities.value.find(e => e.entity_id === props.selectedEntityId)
  if (!entity)
    return null
  const tNodes = props.data.tree_nodes.filter(n => n.entity_id === props.selectedEntityId)
  const map: Record<number | string, any> = {}
  const rootTreeNode = tNodes.find(n => n.node_type === 'root')
  const rootNode = {
    id: `e_${entity.id}`,
    type: 'entity',
    name: entity.canonical_ref,
    desc: entity.desc,
    aliases: entity.aliases,
    treeRootSummary: rootTreeNode?.description ?? null,
    children: [] as any[],
  }
  tNodes.forEach((n) => {
    map[n.id] = {
      id: `t_${n.id}`,
      type: 'treenode',
      name: n.description || n.node_type || 'Node',
      node_type: n.node_type,
      parent_id: n.parent_id,
      fact_id: n.fact_id,
      children: [] as any[],
    }
  })
  tNodes.forEach((n) => {
    const node = map[n.id]
    if (n.parent_id && map[n.parent_id])
      map[n.parent_id].children.push(node)
    else
      rootNode.children.push(node)
    if (n.fact_id && n.fact_text !== null) {
      node.factData = {
        id: n.fact_id,
        text: n.fact_text,
        batch_index: n.fact_batch_index,
        fact_ts: n.fact_ts,
        status: n.fact_status,
      } as FactPageItem
    }
  })
  entity.orphan_facts.forEach((fact) => {
    rootNode.children.push({
      id: `f_${fact.id}_orphan`,
      type: 'fact',
      name: formatFactText(fact.text),
      factData: fact,
      isOrphan: true,
      children: [],
    })
  })
  function pruneRootNodes(node: any) {
    if (!node.children)
      return
    const newChildren = []
    for (const child of node.children) {
      if (child.type === 'treenode' && child.node_type === 'root') {
        pruneRootNodes(child)
        newChildren.push(...child.children)
        if (child.factData) {
          newChildren.push({
            id: `f_${child.factData.id}_hoisted`,
            type: 'fact',
            name: formatFactText(child.factData.text),
            factData: child.factData,
            children: [],
          })
        }
      }
      else {
        pruneRootNodes(child)
        newChildren.push(child)
      }
    }
    node.children = newChildren
  }
  pruneRootNodes(rootNode)
  function normalizeFactsAndLeaves(node: any) {
    if (!node.children)
      return
    if (node.type === 'treenode' && node.factData && node.children.length === 0) {
      node.type = 'fact'
      node.prefixLabel = node.name
      node.name = formatFactText(node.factData.text)
    }
    else if (node.type === 'treenode' && node.factData) {
      node.children.push({
        id: `f_${node.factData.id}_${node.id}`,
        type: 'fact',
        name: formatFactText(node.factData.text),
        factData: node.factData,
        children: [],
      })
      delete node.factData
    }
    node.children.forEach(normalizeFactsAndLeaves)
  }
  normalizeFactsAndLeaves(rootNode)
  function sortTree(node: any) {
    if (!node.children)
      return
    node.children.sort((a: any, b: any) => {
      const w = (n: any) => n.type === 'treenode' ? 1 : n.isOrphan ? 3 : 2
      return w(a) - w(b)
    })
    node.children.forEach(sortTree)
  }
  sortTree(rootNode)
  return rootNode
})

const flatTreeData = computed(() => {
  const rootNode = sortedTreeRoot.value
  if (!rootNode)
    return []
  const flatList: any[] = []
  function recurse(node: any, depth: number) {
    const hasChildren = node.children && node.children.length > 0
    const isExpanded = node.type === 'entity' || expandedNodes.value.has(node.id)
    flatList.push({ ...node, depth, hasChildren, isExpanded })
    if (!isExpanded || !hasChildren)
      return
    node.children.forEach((child: any) => recurse(child, depth + 1))
  }
  recurse(rootNode, 0)
  return flatList
})

function toggleExpand(node: any) {
  if (!node.hasChildren)
    return
  if (expandedNodes.value.has(node.id))
    expandedNodes.value.delete(node.id)
  else
    expandedNodes.value.add(node.id)
}
</script>

<template>
  <div class="entities-tab">
    <div v-if="data.entities.length === 0" class="center-msg">
      <div class="empty-state">
        <ListTree v-if="entityViewMode === 'tree'" :size="30" class="empty-icon" />
        <Focus v-else-if="entityViewMode === 'graph'" :size="30" class="empty-icon" />
        <Globe v-else :size="30" class="empty-icon" />
        <p class="empty-desc">Entity graphs are built from facts during ingestion.</p>
      </div>
    </div>
    <template v-else>
      <EntityLocalGraph
        v-if="entityViewMode === 'graph'"
        :data="data"
        :selected-entity-id="selectedEntityId"
      />
      <EntityGlobalGraph
        v-else-if="entityViewMode === 'overview'"
        :data="data"
        :selected-entity-id="selectedEntityId"
        @update:selected-entity-id="(id: string) => emit('update:selectedEntityId', id)"
      />
      <EntityTree
        v-else
        :flat-tree-data="flatTreeData"
        :has-entities="true"
        @toggle-expand="toggleExpand"
      />
    </template>
  </div>
</template>

<style scoped>
.entities-tab { flex: 1; display: flex; flex-direction: column; overflow: hidden; padding: 0.5rem; }
.center-msg { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--c-text-muted); }
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 5px; max-width: 280px; padding: 8px; }
.empty-icon { width: 30px; height: 30px; color: #cbd5e1; margin-bottom: 6px; }
.empty-desc { font-size: 0.875rem; color: #94a3b8; margin: 0; text-align: center; line-height: 1.5; }
</style>
