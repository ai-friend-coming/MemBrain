<script setup lang="ts">
import type { MemoryGraph } from '@/types'
import * as d3 from 'd3'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getEntityTextMetrics, getRgba, HitColorManager } from '@/lib/canvasGraph'

interface CNode extends d3.SimulationNodeDatum {
  id: string
  label: string
  fullText?: string
  type: 'entity' | 'aspect' | 'fact'
  entityId: string
  r: number
  fill: string
  stroke: string
  strokeWidth: number
  hitColor: string
  factCount: number
  targetX?: number
  targetY?: number
}

interface ArcParams {
  cx: number
  cy: number
  r: number
  a1: number
  a2: number
}

interface CLink {
  source: CNode
  target: CNode
  type: 'tree' | 'shared'
  arc?: ArcParams | null
}

const props = defineProps<{
  data: MemoryGraph
  selectedEntityId: string | null
}>()

const emit = defineEmits<{
  'update:selectedEntityId': [id: string]
}>()

const tooltip = ref<{
  title: string
  lines: string[]
  type: string
} | null>(null)

const containerRef = ref<HTMLDivElement | null>(null)

let canvasEdges: HTMLCanvasElement | null = null
let canvasNodes: HTMLCanvasElement | null = null
let canvasHover: HTMLCanvasElement | null = null
let canvasHidden: HTMLCanvasElement | null = null
let svgEl: SVGSVGElement | null = null

let _ctxEdges: CanvasRenderingContext2D | null = null
let _ctxNodes: CanvasRenderingContext2D | null = null
let _ctxHover: CanvasRenderingContext2D | null = null
let _ctxHidden: CanvasRenderingContext2D | null = null

let w = 800
let h = 600
const dpr = window.devicePixelRatio || 1

const nodes: CNode[] = []
const links: CLink[] = []
const hitColors = new HitColorManager<CNode>()
const childrenOf = new Map<string, string[]>()
const parentsOf = new Map<string, string[]>()

// Zoom and hover state
let currentTransform = d3.zoomIdentity
let hoveredNode: CNode | null = null
const highlightedNodeIds = new Set<string>()
const highlightedLinks = new Set<CLink>()

// Zoom behavior
let zoomBehavior: d3.ZoomBehavior<HTMLCanvasElement, unknown> | null = null

let oldW = w
let oldH = h

function resizeAll() {
  if (!containerRef.value)
    return
  const rect = containerRef.value.getBoundingClientRect()
  w = rect.width || 800
  h = rect.height || 600

  // Maintain center zoom point across resizes
  if (oldW > 0 && oldH > 0 && canvasHover) {
    const dx = (w - oldW) / 2
    const dy = (h - oldH) / 2
    currentTransform = d3.zoomIdentity
      .translate(currentTransform.x + dx, currentTransform.y + dy)
      .scale(currentTransform.k)
    d3.select(canvasHover).property('__zoom', currentTransform)
  }
  oldW = w
  oldH = h

  // Display canvases: pixel buffer = w*dpr × h*dpr, drawn in CSS coords
  for (const c of [canvasEdges, canvasNodes, canvasHover]) {
    if (!c)
      continue
    c.width = w * dpr
    c.height = h * dpr
    c.style.width = `${w}px`
    c.style.height = `${h}px`
    const ctx = c.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0) // reset to dpr base
  }
  // Hidden canvas: pixel buffer = w × h (CSS pixels), no DPR scaling
  if (canvasHidden) {
    canvasHidden.width = w
    canvasHidden.height = h
    canvasHidden.style.width = `${w}px`
    canvasHidden.style.height = `${h}px`
  }
  if (svgEl) {
    svgEl.setAttribute('width', String(w))
    svgEl.setAttribute('height', String(h))
  }
  updateZoomBounds()
}

function updateZoomBounds() {
  if (!zoomBehavior || nodes.length === 0)
    return
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  for (const n of nodes) {
    if (n.x! < minX)
      minX = n.x!
    if (n.x! > maxX)
      maxX = n.x!
    if (n.y! < minY)
      minY = n.y!
    if (n.y! > maxY)
      maxY = n.y!
  }

  if (minX === Infinity)
    return

  const paddingX = w / 2
  const paddingY = h / 2

  if (zoomBehavior) {
    zoomBehavior.translateExtent([
      [minX - paddingX, minY - paddingY],
      [maxX + paddingX, maxY + paddingY],
    ])
  }
}

function zoomToFit(duration = 0) {
  if (!zoomBehavior || nodes.length === 0 || !canvasHover || w <= 0 || h <= 0)
    return

  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  for (const n of nodes) {
    const r = (n.r || 10) * 1.5 // buffer for text
    if (n.x! - r < minX)
      minX = n.x! - r
    if (n.x! + r > maxX)
      maxX = n.x! + r
    if (n.y! - r < minY)
      minY = n.y! - r
    if (n.y! + r > maxY)
      maxY = n.y! + r
  }

  if (minX === Infinity)
    return

  const dx = Math.max(1, maxX - minX)
  const dy = Math.max(1, maxY - minY)
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2

  const pad = 60
  const scaleOriginal = Math.min((w - pad * 2) / dx, (h - pad * 2) / dy)
  const scale = Math.max(0.1, Math.min(scaleOriginal, 2.0))

  const t = d3.zoomIdentity.translate(w / 2 - scale * cx, h / 2 - scale * cy).scale(scale)

  if (duration > 0) {
    d3.select(canvasHover).transition().duration(duration).call(zoomBehavior.transform as any, t)
  }
  else {
    d3.select(canvasHover).call(zoomBehavior.transform as any, t)
  }
}

let resizeObserver: ResizeObserver | null = null
// eslint-disable-next-line prefer-const
let renderTimer: ReturnType<typeof setTimeout> | null = null

function onCanvasMousemove(e: MouseEvent) {
  if (!_ctxHidden)
    return
  const rect = containerRef.value?.getBoundingClientRect()
  if (!rect)
    return
  const mx = Math.round(e.clientX - rect.left)
  const my = Math.round(e.clientY - rect.top)
  if (mx < 0 || my < 0 || mx >= w || my >= h)
    return
  const pixel = _ctxHidden.getImageData(mx, my, 1, 1).data
  const found = hitColors.lookup(pixel[0], pixel[1], pixel[2])
  if (found !== hoveredNode) {
    hoveredNode = found
    updateHighlight()
    draw()
  }

  if (hoveredNode) {
    let title = hoveredNode.label
    const lines = []
    if (hoveredNode.type === 'fact') {
      title = ''
      lines.push(hoveredNode.fullText || hoveredNode.label)
    }
    else if (hoveredNode.fullText && hoveredNode.fullText !== hoveredNode.label) {
      lines.push(hoveredNode.fullText)
    }

    tooltip.value = {
      title,
      lines,
      type: hoveredNode.type,
    }
  }
  else {
    tooltip.value = null
  }
}

function onCanvasMouseleave() {
  if (hoveredNode !== null) {
    hoveredNode = null
    updateHighlight()
    draw()
  }
  tooltip.value = null
}

function onCanvasClick(e: MouseEvent) {
  if (!_ctxHidden)
    return
  const rect = containerRef.value?.getBoundingClientRect()
  if (!rect)
    return
  const mx = Math.round(e.clientX - rect.left)
  const my = Math.round(e.clientY - rect.top)
  if (mx < 0 || my < 0 || mx >= w || my >= h)
    return
  const pixel = _ctxHidden.getImageData(mx, my, 1, 1).data
  const found = hitColors.lookup(pixel[0], pixel[1], pixel[2])

  if (!found) {
    return
  }

  if (found.type === 'entity') {
    emit('update:selectedEntityId', found.entityId)
  }
}

onMounted(() => {
  const container = containerRef.value!

  function makeCanvas(id: string, zIndex: number, hidden = false): HTMLCanvasElement {
    const el = document.createElement('canvas')
    el.id = id
    Object.assign(el.style, {
      position: 'absolute',
      top: '0',
      left: '0',
      zIndex: String(zIndex),
      display: hidden ? 'none' : 'block',
    })
    container.appendChild(el)
    return el
  }

  canvasEdges = makeCanvas('cg-edges', 1)
  canvasNodes = makeCanvas('cg-nodes', 2)
  canvasHover = makeCanvas('cg-hover', 3)
  canvasHidden = makeCanvas('cg-hidden', 0, true)
  _ctxEdges = canvasEdges.getContext('2d')!
  _ctxNodes = canvasNodes.getContext('2d')!
  _ctxHover = canvasHover.getContext('2d')!
  _ctxHidden = canvasHidden.getContext('2d', { willReadFrequently: true })!

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  Object.assign(svg.style, {
    position: 'absolute',
    top: '0',
    left: '0',
    zIndex: '4',
    pointerEvents: 'none',
  })
  container.appendChild(svg)
  svgEl = svg

  resizeAll()
  render()

  resizeObserver = new ResizeObserver(() => {
    resizeAll()
    if (nodes.length === 0) {
      render()
    }
    else {
      // Nodes already positioned — just redraw
      draw()
    }
  })
  resizeObserver.observe(container)

  // Mouse events on canvasHover (receives all pointer events since SVG is pointer-events:none)
  canvasHover!.addEventListener('mousemove', onCanvasMousemove)
  canvasHover!.addEventListener('mouseleave', onCanvasMouseleave)
  canvasHover!.addEventListener('click', onCanvasClick)
})

onBeforeUnmount(() => {
  stopAnimation()
  if (renderTimer)
    clearTimeout(renderTimer)
  resizeObserver?.disconnect()
  if (canvasHover) {
    d3.select(canvasHover).on('.zoom', null)
    canvasHover.removeEventListener('mousemove', onCanvasMousemove)
    canvasHover.removeEventListener('mouseleave', onCanvasMouseleave)
    canvasHover.removeEventListener('click', onCanvasClick)
  }
  // Free pixel buffers immediately (don't wait for GC)
  for (const c of [canvasEdges, canvasNodes, canvasHover, canvasHidden]) {
    if (c) {
      c.width = 1
      c.height = 1
    }
  }
  canvasEdges = canvasNodes = canvasHover = canvasHidden = null
  _ctxEdges = _ctxNodes = _ctxHover = _ctxHidden = null
})

watch(() => props.data, () => render())

watch(() => props.selectedEntityId, (id) => {
  if (!id || !zoomBehavior || !canvasHover || nodes.length === 0)
    return
  const node = nodes.find(n => n.type === 'entity' && n.entityId === id)
  if (!node || node.x == null)
    return
  const scale = Math.max(currentTransform.k, 0.6)
  const t = d3.zoomIdentity
    .translate(w / 2 - node.x * scale, h / 2 - node.y * scale)
    .scale(scale)
  d3.select(canvasHover).transition().duration(400).call(zoomBehavior.transform as any, t)
})

function buildGraph() {
  nodes.length = 0
  links.length = 0
  hitColors.clear()
  childrenOf.clear()
  parentsOf.clear()
  const d = props.data

  const d3Colors = d3.scaleOrdinal([
    '#3b82f6',
    '#f43f5e',
    '#f59e0b',
    '#8b5cf6',
    '#0ea5e9',
    '#ec4899',
    '#f97316',
    '#6366f1',
    '#84cc16',
    '#d946ef',
  ])

  // Identify entity roots and nested roots
  const rootByEntity = new Map<string, number>()
  d.tree_nodes.forEach((tn) => {
    if (tn.node_type === 'root') {
      if (!rootByEntity.has(tn.entity_id) || tn.parent_id == null)
        rootByEntity.set(tn.entity_id, tn.id)
    }
  })
  const entityRootIds = new Set(rootByEntity.values())
  const nestedRootIds = new Set<number>()
  d.tree_nodes.forEach((tn) => {
    if (tn.node_type === 'root' && !entityRootIds.has(tn.id))
      nestedRootIds.add(tn.id)
  })
  const treeNodeById = new Map(d.tree_nodes.map(tn => [tn.id, tn]))

  function getEffectiveParentId(parentId: number | null): number | null {
    let cur = parentId
    while (cur != null && nestedRootIds.has(cur))
      cur = treeNodeById.get(cur)?.parent_id ?? null
    return cur
  }

  // Deduplicate entities (take first occurrence)
  const latestEntities = new Map<string, typeof d.entities[0]>()
  d.entities.forEach((e) => {
    if (!latestEntities.has(e.entity_id))
      latestEntities.set(e.entity_id, e)
  })
  const entityNumericId = new Map<string, number>()
  latestEntities.forEach(e => entityNumericId.set(e.entity_id, e.id))

  // Count unique facts per entity for entity node sizing
  const factCountByEntity = new Map<string, Set<number>>()
  d.tree_nodes.forEach((tn) => {
    if (tn.fact_id == null)
      return
    if (!factCountByEntity.has(tn.entity_id))
      factCountByEntity.set(tn.entity_id, new Set())
    factCountByEntity.get(tn.entity_id)!.add(tn.fact_id)
  })

  const nodeById = new Map<string, CNode>()

  // Entity nodes
  latestEntities.forEach((e) => {
    const fc = factCountByEntity.get(e.entity_id)?.size ?? 0
    const color = d3Colors(e.entity_id)
    const r = Math.round(20 + Math.sqrt(fc) * 7)
    const node: CNode = {
      id: `e_${e.id}`,
      label: e.canonical_ref,
      fullText: e.desc,
      type: 'entity',
      entityId: e.entity_id,
      r,
      fill: color,
      stroke: color,
      strokeWidth: 4.5,
      hitColor: hitColors.nextColor(),
      factCount: fc,
    }
    nodes.push(node)
    nodeById.set(node.id, node)
    hitColors.register(node.hitColor, node)
  })

  // Precompute: aspect tree_node id → count of direct fact children
  const aspectFactChildCount = new Map<number, number>()
  d.tree_nodes.forEach((c) => {
    if (c.fact_id == null)
      return
    const effParent = getEffectiveParentId(c.parent_id)
    if (effParent == null)
      return
    aspectFactChildCount.set(effParent, (aspectFactChildCount.get(effParent) ?? 0) + 1)
  })

  // Collect fact→parent mapping while iterating tree_nodes
  // factParents: fact_id → list of parent graph node IDs (aspect or entity)
  const factParents = new Map<number, string[]>()

  d.tree_nodes.forEach((tn) => {
    if (entityRootIds.has(tn.id) || nestedRootIds.has(tn.id))
      return

    const effParent = getEffectiveParentId(tn.parent_id)
    if (effParent == null)
      return

    const entityRoot = rootByEntity.get(tn.entity_id)
    const numId = entityNumericId.get(tn.entity_id)
    const parentGraphId = effParent === entityRoot
      ? `e_${numId}`
      : `t_${effParent}`

    if (tn.fact_id != null) {
      if (!factParents.has(tn.fact_id))
        factParents.set(tn.fact_id, [])
      const existing = factParents.get(tn.fact_id)!
      if (!existing.includes(parentGraphId))
        existing.push(parentGraphId)
      return
    }

    // Aspect node — count child facts for sizing
    const childFactCount = aspectFactChildCount.get(tn.id) ?? 0
    const color = d3Colors(tn.entity_id)
    const r = Math.round(10 + Math.sqrt(childFactCount) * 4)
    const raw = tn.description ?? tn.node_type ?? 'Aspect'
    const label = raw.length > 30 ? `${raw.substring(0, 30)}…` : raw
    const node: CNode = {
      id: `t_${tn.id}`,
      label,
      fullText: raw,
      type: 'aspect',
      entityId: tn.entity_id,
      r,
      fill: color,
      stroke: color,
      strokeWidth: 0,
      hitColor: hitColors.nextColor(),
      factCount: 0,
    }
    nodes.push(node)
    nodeById.set(node.id, node)
    hitColors.register(node.hitColor, node)
  })

  // Aspect → parent tree links
  d.tree_nodes.forEach((tn) => {
    if (entityRootIds.has(tn.id) || nestedRootIds.has(tn.id))
      return
    if (tn.fact_id != null)
      return // leaves handled via factParents
    const effParent = getEffectiveParentId(tn.parent_id)
    if (effParent == null)
      return
    const entityRoot = rootByEntity.get(tn.entity_id)
    const numId = entityNumericId.get(tn.entity_id)
    const parentGraphId = effParent === entityRoot ? `e_${numId}` : `t_${effParent}`
    const src = nodeById.get(parentGraphId)
    const tgt = nodeById.get(`t_${tn.id}`)
    if (!src || !tgt)
      return
    links.push({ source: src, target: tgt, type: 'tree' })
  })

  // Collect orphan facts from fact_refs (not in any tree_node)
  const usedFactIds = new Set(d.tree_nodes.filter(tn => tn.fact_id != null).map(tn => tn.fact_id!))
  d.fact_refs.forEach((fr) => {
    if (usedFactIds.has(fr.fact_id))
      return
    const numId = entityNumericId.get(fr.entity_id)
    if (numId == null)
      return
    const parentGraphId = `e_${numId}`
    if (!factParents.has(fr.fact_id))
      factParents.set(fr.fact_id, [])
    const existing = factParents.get(fr.fact_id)!
    if (!existing.includes(parentGraphId))
      existing.push(parentGraphId)
  })

  // Collect all unique facts (text from tree_nodes; fallback to alias_text from fact_refs)
  const allFacts = new Map<number, string>()
  d.tree_nodes.forEach((tn) => {
    if (tn.fact_id != null && tn.fact_text != null && !allFacts.has(tn.fact_id))
      allFacts.set(tn.fact_id, tn.fact_text)
  })
  d.fact_refs.forEach((fr) => {
    if (!allFacts.has(fr.fact_id))
      allFacts.set(fr.fact_id, fr.alias_text || `Fact ${fr.fact_id}`)
  })

  allFacts.forEach((text, factId) => {
    const raw = text.length > 30 ? `${text.substring(0, 30)}…` : text
    const node: CNode = {
      id: `f_${factId}`,
      label: raw,
      fullText: text,
      type: 'fact',
      entityId: '',
      r: 6,
      fill: '#10b981',
      stroke: '#10b981',
      strokeWidth: 0,
      hitColor: hitColors.nextColor(),
      factCount: 0,
    }
    nodes.push(node)
    nodeById.set(node.id, node)
    hitColors.register(node.hitColor, node)

    const parents = factParents.get(factId) ?? []
    parents.forEach((parentId) => {
      const src = nodeById.get(parentId)
      if (!src)
        return
      links.push({ source: src, target: node, type: 'tree' })
    })
  })

  // Build adjacency maps for hover traversal
  links.forEach((l) => {
    const sid = (l.source as CNode).id
    const tid = (l.target as CNode).id
    if (!childrenOf.has(sid))
      childrenOf.set(sid, [])
    childrenOf.get(sid)!.push(tid)
    if (!parentsOf.has(tid))
      parentsOf.set(tid, [])
    parentsOf.get(tid)!.push(sid)
  })
}

function calcArcParams(
  p1: { x: number, y: number },
  p2: { x: number, y: number },
): ArcParams | null {
  const dx = p2.x - p1.x
  const dy = p2.y - p1.y
  const dist = Math.sqrt(dx * dx + dy * dy)
  if (dist < 1)
    return null
  const r = dist * 2
  const mx = (p1.x + p2.x) / 2
  const my = (p1.y + p2.y) / 2
  const px = -(p2.y - p1.y)
  const py = p2.x - p1.x
  const norm = Math.sqrt(px * px + py * py)
  const pxn = px / norm
  const pyn = py / norm
  const dpmp1 = Math.sqrt((mx - p1.x) ** 2 + (my - p1.y) ** 2)
  const sinA = dpmp1 / r
  if (sinA > 1)
    return null
  const cosA = Math.sqrt(1 - sinA * sinA)
  const d = r * cosA
  const arcCx = mx - pxn * d
  const arcCy = my - pyn * d
  return {
    cx: arcCx,
    cy: arcCy,
    r,
    a1: Math.atan2(p1.y - arcCy, p1.x - arcCx),
    a2: Math.atan2(p2.y - arcCy, p2.x - arcCx),
  }
}

function computeLayout() {
  if (nodes.length === 0)
    return
  const cx = w / 2
  const cy = h / 2
  const R_outer = Math.min(w, h) * 0.32

  const entityNodes = nodes.filter(n => n.type === 'entity')
  const totalFacts = entityNodes.reduce((s, e) => s + Math.max(e.factCount, 1), 0)
  let accumulated = 0
  entityNodes.forEach((e) => {
    const frac = Math.max(e.factCount, 1) / totalFacts
    const angle = (accumulated + frac / 2) * Math.PI * 2 - Math.PI / 2
    e.targetX = cx + R_outer * Math.cos(angle)
    e.targetY = cy + R_outer * Math.sin(angle)
    accumulated += frac
  })

  const entityTargetById = new Map(
    entityNodes.map(e => [e.entityId, { x: e.targetX!, y: e.targetY! }]),
  )
  nodes.filter(n => n.type === 'aspect').forEach((a) => {
    const t = entityTargetById.get(a.entityId)
    if (t) {
      a.targetX = t.x
      a.targetY = t.y
    }
  })

  // ── 3. Force simulation ──────────────────────────────────────
  const sim = d3.forceSimulation<CNode>(nodes)
    .force('link', d3.forceLink<CNode, d3.SimulationLinkDatum<CNode>>(links as d3.SimulationLinkDatum<CNode>[])
      .id(d => d.id)
      .distance((l) => {
        const t = l.target as CNode
        if (t.type === 'fact')
          return 55
        if (t.type === 'aspect')
          return 85
        return 140
      })
      .strength(0.3))
    .force('charge', d3.forceManyBody<CNode>()
      .strength(d => d.type === 'entity' ? -300 : d.type === 'aspect' ? -60 : -20))
    .force('collide', d3.forceCollide<CNode>().radius(d => d.r + 5))
    .force('x', d3.forceX<CNode>(d => d.targetX ?? cx)
      .strength(d => d.type === 'entity' ? 0.12 : d.type === 'aspect' ? 0.04 : 0))
    .force('y', d3.forceY<CNode>(d => d.targetY ?? cy)
      .strength(d => d.type === 'entity' ? 0.12 : d.type === 'aspect' ? 0.04 : 0))
    .stop()

  for (let i = 0; i < 500 && sim.alpha() > 0.003; i++) sim.tick()
  sim.stop()

  nodes.forEach((n) => {
    n.fx = n.x
    n.fy = n.y
  })

  updateZoomBounds()

  links.forEach((l) => {
    const src = l.source as CNode
    const tgt = l.target as CNode
    l.arc = calcArcParams(
      { x: src.x ?? 0, y: src.y ?? 0 },
      { x: tgt.x ?? 0, y: tgt.y ?? 0 },
    )
  })
}

let animationFrameId: number | null = null
let flowDirection: 'down' | 'up' = 'down'

function startAnimation() {
  if (animationFrameId === null) {
    animationFrameId = requestAnimationFrame(animateLoop)
  }
}

function stopAnimation() {
  if (animationFrameId !== null) {
    cancelAnimationFrame(animationFrameId)
    animationFrameId = null
  }
}

function animateLoop() {
  if (!hoveredNode) {
    stopAnimation()
    return
  }

  drawEdges()
  animationFrameId = requestAnimationFrame(animateLoop)
}

function drawEdges() {
  if (!_ctxEdges)
    return
  const t = currentTransform
  _ctxEdges.clearRect(0, 0, w, h)
  _ctxEdges.save()
  _ctxEdges.translate(t.x, t.y)
  _ctxEdges.scale(t.k, t.k)
  for (const link of links) {
    const sx = (link.source as CNode).x ?? 0
    const sy = (link.source as CNode).y ?? 0
    const tx2 = (link.target as CNode).x ?? 0
    const ty2 = (link.target as CNode).y ?? 0

    const highlighted = hoveredNode != null && highlightedLinks.has(link)
    const dimmed = hoveredNode != null && !highlighted

    const arc = link.arc
    _ctxEdges.beginPath()
    _ctxEdges.moveTo(sx, sy)
    if (arc) {
      _ctxEdges.arc(arc.cx, arc.cy, arc.r, arc.a1, arc.a2, true)
    }
    else {
      const freshArc = calcArcParams({ x: sx, y: sy }, { x: tx2, y: ty2 })
      if (freshArc) {
        _ctxEdges.arc(freshArc.cx, freshArc.cy, freshArc.r, freshArc.a1, freshArc.a2, true)
      }
      else {
        _ctxEdges.lineTo(tx2, ty2)
      }
    }

    if (highlighted) {
      // 1. Calculate approximate line length
      const dist = Math.sqrt((tx2 - sx) ** 2 + (ty2 - sy) ** 2)
      // Keep duration baseline around 1.5s but cap it at 2.5s maximum.
      // For very long lines, hitting the 2.5s cap means they will visibly accelerate to finish in time.
      const duration = Math.max(1500, Math.min(dist * 8, 2500))

      // 2. Generate a stable pseudo-random offset based on coordinates
      // This ensures each line starts its "pulse" at a slightly different time
      const seed = Math.abs(sx * 13 + sy * 31 + tx2 * 17 + ty2 * 7) % 2000

      const now = performance.now()
      const phase = flowDirection === 'down'
        ? ((now + seed) % duration) / duration
        : 1 - (((now + seed) % duration) / duration)

      const c = (link.source as CNode).fill
      const grad = _ctxEdges.createLinearGradient(sx, sy, tx2, ty2)

      const p1 = Math.max(0, phase - 0.2)
      const p2 = phase
      const p3 = Math.min(1, phase + 0.2)

      const glowStr = getRgba(c, 1)
      const fadeStr = getRgba(c, 0.05)

      // Gradient wave stops
      grad.addColorStop(0, fadeStr)
      if (p1 > 0)
        grad.addColorStop(p1, fadeStr)
      grad.addColorStop(p2, glowStr)
      if (p3 < 1)
        grad.addColorStop(p3, fadeStr)
      grad.addColorStop(1, fadeStr)

      // Draw the wave (soft wide glow)
      _ctxEdges.globalAlpha = 1.0
      _ctxEdges.strokeStyle = grad
      _ctxEdges.lineWidth = Math.min(2.5, 3.5 / t.k)
      _ctxEdges.stroke()

      // Draw solid core line
      _ctxEdges.strokeStyle = getRgba(c, 0.8)
      _ctxEdges.lineWidth = Math.min(1.0, 1.5 / t.k)
      _ctxEdges.stroke()
    }
    else {
      _ctxEdges.globalAlpha = dimmed ? 0.03 : 0.25
      _ctxEdges.strokeStyle = (link.source as CNode).fill
      _ctxEdges.lineWidth = Math.min(0.8, 1.5 / t.k)
      _ctxEdges.stroke()
    }
  }
  _ctxEdges.setLineDash([])
  _ctxEdges.restore()
}

function draw() {
  if (!_ctxEdges || !_ctxNodes || !_ctxHover || !_ctxHidden)
    return
  const t = currentTransform

  drawEdges()

  // Clear all other layers
  _ctxNodes.clearRect(0, 0, w, h)
  _ctxHover.clearRect(0, 0, w, h)
  _ctxHidden.clearRect(0, 0, w, h)

  // ── Nodes (display canvas, dpr-scaled base) ──────────────────────
  _ctxNodes.save()
  _ctxNodes.translate(t.x, t.y)
  _ctxNodes.scale(t.k, t.k)
  for (const node of nodes) {
    const dimmed = hoveredNode != null && !highlightedNodeIds.has(node.id)
    _ctxNodes.globalAlpha = dimmed ? 0.07 : 1.0

    _ctxNodes.beginPath()
    if (node.type === 'fact') {
      const dr = node.r * 1.1
      _ctxNodes.moveTo((node.x ?? 0), (node.y ?? 0) - dr)
      _ctxNodes.lineTo((node.x ?? 0) + dr, (node.y ?? 0))
      _ctxNodes.lineTo((node.x ?? 0), (node.y ?? 0) + dr)
      _ctxNodes.lineTo((node.x ?? 0) - dr, (node.y ?? 0))
      _ctxNodes.closePath()
    }
    else {
      _ctxNodes.arc(node.x ?? 0, node.y ?? 0, node.r, 0, Math.PI * 2)
    }

    if (node.type === 'entity') {
      _ctxNodes.fillStyle = '#ffffff'
      _ctxNodes.fill()
      if (node.strokeWidth > 0) {
        _ctxNodes.strokeStyle = node.stroke
        _ctxNodes.lineWidth = Math.min(node.strokeWidth, 4.0 / t.k)
        _ctxNodes.stroke()
      }
    }
    else if (node.type === 'aspect') {
      const visR = node.r * t.k
      let fillAlpha = 1.0
      if (visR > 6) {
        fillAlpha = Math.max(0.8, 1.0 - ((visR - 6) / 20) * 0.2)
      }

      _ctxNodes.fillStyle = '#ffffff'
      _ctxNodes.fill()

      _ctxNodes.fillStyle = getRgba(node.fill, fillAlpha)
      _ctxNodes.fill()

      _ctxNodes.strokeStyle = node.fill
      _ctxNodes.lineWidth = Math.min(2.5, 3.0 / t.k)
      _ctxNodes.stroke()
    }
    else {
      _ctxNodes.fillStyle = node.fill
      _ctxNodes.fill()
      if (node.strokeWidth > 0) {
        _ctxNodes.strokeStyle = node.stroke
        _ctxNodes.lineWidth = Math.min(node.strokeWidth, 2.0 / t.k)
        _ctxNodes.stroke()
      }
    }

    if (node.type === 'entity') {
      const minVisSize = 10
      const maxVisSize = 72

      const text = node.label.toUpperCase()
      const metrics = getEntityTextMetrics(_ctxNodes, text)
      const effR = node.r * 0.95

      let optimalCanvasFontSize = effR / metrics.radiusFactor

      const capFontSizeCanvas = node.r * 0.65
      optimalCanvasFontSize = Math.min(optimalCanvasFontSize, capFontSizeCanvas)

      const optimalVisSize = optimalCanvasFontSize * t.k

      if (optimalVisSize >= minVisSize) {
        const visualFontSize = Math.min(optimalVisSize, maxVisSize)
        const canvasFontSize = visualFontSize / t.k

        _ctxNodes.fillStyle = '#1e293b'
        _ctxNodes.font = `600 ${canvasFontSize}px 'Oswald', 'Roboto Condensed', 'DIN Alternate', 'Arial Narrow', sans-serif`
        _ctxNodes.textAlign = 'center'
        _ctxNodes.textBaseline = 'middle'

        if (metrics.layout === 'single') {
          _ctxNodes.fillText(text, node.x ?? 0, node.y ?? 0)
        }
        else {
          _ctxNodes.fillText(metrics.line1, node.x ?? 0, (node.y ?? 0) - canvasFontSize * 0.55)
          _ctxNodes.fillText(metrics.line2, node.x ?? 0, (node.y ?? 0) + canvasFontSize * 0.55)
        }
      }
    }
  }
  _ctxNodes.restore()

  // ── Hidden canvas (CSS-pixel coords, NO dpr scaling) ────────────
  // _ctxHidden uses raw pixel coordinates matching mouse event CSS coords
  _ctxHidden.save()
  _ctxHidden.translate(t.x, t.y)
  _ctxHidden.scale(t.k, t.k)
  for (const node of nodes) {
    _ctxHidden.fillStyle = node.hitColor
    _ctxHidden.beginPath()
    _ctxHidden.arc(node.x ?? 0, node.y ?? 0, node.r + 4, 0, Math.PI * 2)
    _ctxHidden.fill()
  }
  _ctxHidden.restore()
}

function initZoom() {
  if (!canvasHover)
    return
  d3.select(canvasHover).on('.zoom', null)

  zoomBehavior = d3.zoom<HTMLCanvasElement, unknown>()
    .scaleExtent([0.15, 6])
    .on('zoom', (event: d3.D3ZoomEvent<HTMLCanvasElement, unknown>) => {
      currentTransform = event.transform
      tooltip.value = null
      draw()
    })

  d3.select(canvasHover).call(zoomBehavior)
  // Disable double click zoom natively provided by d3.zoom
  d3.select(canvasHover).on('dblclick.zoom', null)
}

function updateHighlight() {
  highlightedNodeIds.clear()
  highlightedLinks.clear()
  if (!hoveredNode) {
    stopAnimation()
    return
  }

  flowDirection = hoveredNode.type === 'fact' ? 'up' : 'down'

  if (hoveredNode.type === 'entity') {
    const queue = [hoveredNode.id]
    let max = 10000
    while (queue.length && max-- > 0) {
      const id = queue.shift()!
      highlightedNodeIds.add(id)
      ;(childrenOf.get(id) ?? []).forEach((c) => {
        if (!highlightedNodeIds.has(c))
          queue.push(c)
      })
    }
  }
  else if (hoveredNode.type === 'fact') {
    const queue = [hoveredNode.id]
    let max = 10000
    while (queue.length && max-- > 0) {
      const id = queue.shift()!
      highlightedNodeIds.add(id)
      ;(parentsOf.get(id) ?? []).forEach((p) => {
        if (!highlightedNodeIds.has(p))
          queue.push(p)
      })
    }
  }
  else if (hoveredNode.type === 'aspect') {
    const qDown = [hoveredNode.id]
    let maxDown = 10000
    while (qDown.length && maxDown-- > 0) {
      const id = qDown.shift()!
      highlightedNodeIds.add(id)
      ;(childrenOf.get(id) ?? []).forEach((c) => {
        if (!highlightedNodeIds.has(c))
          qDown.push(c)
      })
    }
    const qUp = [hoveredNode.id]
    let maxUp = 10000
    while (qUp.length && maxUp-- > 0) {
      const id = qUp.shift()!
      highlightedNodeIds.add(id)
      ;(parentsOf.get(id) ?? []).forEach((p) => {
        if (!highlightedNodeIds.has(p))
          qUp.push(p)
      })
    }
  }

  // Mark links that connect two highlighted nodes
  links.forEach((l) => {
    const sid = (l.source as CNode).id
    const tid = (l.target as CNode).id
    if (highlightedNodeIds.has(sid) && highlightedNodeIds.has(tid))
      highlightedLinks.add(l)
  })

  startAnimation()
}

function render() {
  if (!zoomBehavior)
    initZoom()
  buildGraph()
  computeLayout()
  draw()
  if (!props.selectedEntityId) {
    zoomToFit(0)
  }
  else {
    const node = nodes.find(n => n.type === 'entity' && n.entityId === props.selectedEntityId)
    if (node && canvasHover) {
      const scale = 1.0
      const t = d3.zoomIdentity.translate(w / 2 - node.x! * scale, h / 2 - node.y! * scale).scale(scale)
      d3.select(canvasHover).call(zoomBehavior.transform as any, t)
    }
  }
  updateHighlight()
}
</script>

<template>
  <div ref="containerRef" class="constellation-graph">
    <div
      v-if="tooltip"
      class="cg-tooltip"
    >
      <div v-if="tooltip.title" class="cg-tooltip__title">
        {{ tooltip.title }}
      </div>
      <div v-if="tooltip.lines.length > 0" class="cg-tooltip__desc">
        <div v-for="(line, i) in tooltip.lines" :key="i">
          {{ line }}
        </div>
      </div>
      <div class="cg-tooltip__type">
        {{ tooltip.type }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.constellation-graph {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #f8fafc;
}
</style>

<style>
.cg-tooltip {
  position: absolute;
  left: 24px;
  bottom: 24px;
  z-index: 10000;
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
  animation: tooltip-in 0.15s ease-out;
}

@keyframes tooltip-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

.cg-tooltip__title {
  font-weight: 600;
  font-size: 0.875rem;
  margin-bottom: 4px;
  color: #0f172a;
}

.cg-tooltip__desc {
  color: #475569;
  word-break: break-word;
}

.cg-tooltip__type {
  margin-top: 6px;
  font-size: 0.6875rem;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
</style>
