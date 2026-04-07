<script setup lang="ts">
import type { MemoryGraph } from '@/types'
import * as d3 from 'd3'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getEntityTextMetrics, HitColorManager } from '@/lib/canvasGraph'

interface LNode {
  id: string
  label: string
  fullText?: string
  type: 'entity' | 'aspect' | 'fact'
  r: number
  hitColor: string
  factCount: number
  x?: number
  y?: number
  angle?: number
  radius?: number
}

interface LLink {
  source: LNode
  target: LNode
}

const props = defineProps<{
  data: MemoryGraph
  selectedEntityId: string | null
}>()

const tooltip = ref<{ title: string, lines: string[], type: string } | null>(null)
const containerRef = ref<HTMLDivElement | null>(null)

let canvasEdges: HTMLCanvasElement | null = null
let canvasNodes: HTMLCanvasElement | null = null
let canvasHover: HTMLCanvasElement | null = null
let canvasHidden: HTMLCanvasElement | null = null
let _ctxEdges: CanvasRenderingContext2D | null = null
let _ctxNodes: CanvasRenderingContext2D | null = null
let _ctxHover: CanvasRenderingContext2D | null = null
let _ctxHidden: CanvasRenderingContext2D | null = null

let w = 600
let h = 500
const dpr = window.devicePixelRatio || 1
let oldW = w
let oldH = h

const nodes: LNode[] = []
const links: LLink[] = []
const hitColors = new HitColorManager<LNode>()

let currentTransform = d3.zoomIdentity
let zoomBehavior: d3.ZoomBehavior<HTMLCanvasElement, unknown> | null = null
let _resizeObserver: ResizeObserver | null = null

function resizeAll() {
  if (!containerRef.value)
    return
  const rect = containerRef.value.getBoundingClientRect()
  w = rect.width || 600
  h = rect.height || 500
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
  for (const c of [canvasEdges, canvasNodes, canvasHover]) {
    if (!c)
      continue
    c.width = w * dpr
    c.height = h * dpr
    c.style.width = `${w}px`
    c.style.height = `${h}px`
    c.getContext('2d')!.setTransform(dpr, 0, 0, dpr, 0, 0)
  }
  if (canvasHidden) {
    canvasHidden.width = w
    canvasHidden.height = h
    canvasHidden.style.width = `${w}px`
    canvasHidden.style.height = `${h}px`
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
    if ((n.x ?? 0) < minX)
      minX = n.x!
    if ((n.x ?? 0) > maxX)
      maxX = n.x!
    if ((n.y ?? 0) < minY)
      minY = n.y!
    if ((n.y ?? 0) > maxY)
      maxY = n.y!
  }
  if (minX === Infinity)
    return
  const padX = w / 2
  const padY = h / 2
  zoomBehavior.translateExtent([
    [minX - padX * 1.5, minY - padY * 1.5],
    [maxX + padX * 1.5, maxY + padY * 1.5],
  ])
}

function buildTreeAndLayout() {
  nodes.length = 0
  links.length = 0
  hitColors.clear()
  if (!props.selectedEntityId)
    return

  const d = props.data
  const entity = d.entities.find(e => e.entity_id === props.selectedEntityId)
  if (!entity)
    return

  const tns = d.tree_nodes.filter(tn => tn.entity_id === props.selectedEntityId)
  const treeNodeById = new Map(tns.map(tn => [tn.id, tn]))

  let entityRootId: number | null = null
  tns.forEach((tn) => {
    // Treat as root of this local graph if it's node_type='root' AND parent_id=null/itself
    if (tn.node_type === 'root' && (entityRootId == null || tn.parent_id == null))
      entityRootId = tn.id
  })

  // N-layer aspect parent resolution
  const nestedRootIds = new Set<number>()
  tns.forEach((tn) => {
    if (tn.node_type === 'root' && tn.id !== entityRootId)
      nestedRootIds.add(tn.id)
  })

  function getEffParent(parentId: number | null): number | null {
    let cur = parentId
    while (cur != null && nestedRootIds.has(cur)) cur = treeNodeById.get(cur)?.parent_id ?? null
    return cur
  }

  const nodeById = new Map<string, LNode>()

  // 1. Entity Node
  const entityNode: LNode = {
    id: `e_${entity.id}`,
    label: entity.canonical_ref,
    fullText: entity.desc,
    type: 'entity',
    r: 25, // Fallback, will be updated by hierarchy leaf count
    factCount: 0,
    hitColor: hitColors.nextColor(),
  }
  nodeById.set(entityNode.id, entityNode)
  hitColors.register(entityNode.hitColor, entityNode)

  const seenFacts = new Set<number>()
  tns.forEach((tn) => {
    if (tn.id === entityRootId || nestedRootIds.has(tn.id))
      return

    if (tn.fact_id != null) {
      if (seenFacts.has(tn.fact_id))
        return
      seenFacts.add(tn.fact_id)
      const raw = tn.fact_text ?? `Fact ${tn.fact_id}`
      const node: LNode = {
        id: `f_${tn.fact_id}`,
        label: raw.length > 30 ? `${raw.substring(0, 30)}…` : raw,
        fullText: raw,
        type: 'fact',
        r: 5,
        factCount: 1, // leaf value 1
        hitColor: hitColors.nextColor(),
      }
      nodeById.set(node.id, node)
      hitColors.register(node.hitColor, node)
    }
    else {
      const raw = tn.description ?? tn.node_type ?? 'Aspect'
      const node: LNode = {
        id: `t_${tn.id}`,
        label: raw.length > 30 ? `${raw.substring(0, 30)}…` : raw,
        fullText: raw,
        type: 'aspect',
        r: 10, // Fallback
        factCount: 0,
        hitColor: hitColors.nextColor(),
      }
      nodeById.set(node.id, node)
      hitColors.register(node.hitColor, node)
    }
  })

  // Orphan facts
  entity.orphan_facts?.forEach((fact) => {
    const raw = fact.text
    const nodeId = `orphan_${fact.id}`
    if (nodeById.has(nodeId))
      return
    const node: LNode = {
      id: nodeId,
      label: raw.length > 30 ? `${raw.substring(0, 30)}…` : raw,
      fullText: raw,
      type: 'fact',
      r: 5,
      factCount: 1,
      hitColor: hitColors.nextColor(),
    }
    nodeById.set(nodeId, node)
    hitColors.register(node.hitColor, node)
  })

  // Map out children relations
  const childrenMap = new Map<string, string[]>()

  // Normal tree edges
  tns.forEach((tn) => {
    if (tn.id === entityRootId || nestedRootIds.has(tn.id))
      return
    const ep = getEffParent(tn.parent_id)
    if (ep == null)
      return
    const parentId = ep === entityRootId ? `e_${entity.id}` : `t_${ep}`
    const childId = tn.fact_id != null ? `f_${tn.fact_id}` : `t_${tn.id}`

    if (nodeById.has(parentId) && nodeById.has(childId)) {
      if (!childrenMap.has(parentId))
        childrenMap.set(parentId, [])
      childrenMap.get(parentId)!.push(childId)
    }
  })

  // Orphan edges connecting straight to Entity
  entity.orphan_facts?.forEach((fact) => {
    const parentId = `e_${entity.id}`
    const childId = `orphan_${fact.id}`
    if (nodeById.has(childId)) {
      if (!childrenMap.has(parentId))
        childrenMap.set(parentId, [])
      childrenMap.get(parentId)!.push(childId)
    }
  })

  // Build Hierarchy structure to compute sizes and radial branches
  function buildHier(nodeId: string): { dataId: string, children?: any[] } {
    const kids = childrenMap.get(nodeId) || []
    if (kids.length > 0) {
      return { dataId: nodeId, children: kids.map(buildHier) }
    }
    return { dataId: nodeId }
  }

  const rootHierObj = buildHier(entityNode.id)
  const rootHier = d3.hierarchy(rootHierObj)

  // Sum descendants (only fact nodes evaluate to 1)
  rootHier.sum((d: any) => {
    const n = nodeById.get(d.dataId)!
    return n.type === 'fact' ? 1 : 0
  })

  // Set accurate sizes based on accumulated children counts
  rootHier.each((d: any) => {
    const n = nodeById.get(d.data.dataId)!
    n.factCount = d.value || 0
    if (n.type === 'entity') {
      n.r = Math.max(16, 12 + Math.sqrt(n.factCount) * 8)
    }
    else if (n.type === 'aspect') {
      n.r = Math.max(8, 4 + Math.sqrt(n.factCount) * 4)
    }
    else {
      n.r = 4.5
    }
    // Repopulate nodes flat array strictly for canvas iteration
    nodes.push(n)
  })

  // Mathematical Radial Tree mapping
  // Dynamically scale tree span out based on max depths visually.
  const treeLayout = d3.tree<{ dataId: string, children?: any[] }>()
    .size([2 * Math.PI, 100]) // Radius distance overwritten below
    .separation((a, b) => (a.parent === b.parent ? 1.5 : 2.5) / a.depth)

  treeLayout(rootHier)

  // Map generated d3.tree polar variables (d.x=angle) back to cartesian
  // BUT independently assign dynamic safe radii to prevent cramping the Root!
  rootHier.each((d: any) => {
    const n = nodeById.get(d.data.dataId)!

    let targetRadius = 0
    if (d.depth === 1) {
      targetRadius = entityNode.r + n.r + 100 // 100px explicit guaranteed gap from Root
    }
    else if (d.depth > 1) {
      targetRadius = entityNode.r + 100 + (d.depth - 1) * 120 + n.r
    }

    n.angle = d.x // non-crossing angles
    n.radius = targetRadius
    const rAngle = d.x - Math.PI / 2
    n.x = w / 2 + targetRadius * Math.cos(rAngle)
    n.y = h / 2 + targetRadius * Math.sin(rAngle)
  })

  // Controlled Force Relaxation: Provide organic "breathing room" & prevent overlaps
  // while strictly locking them to their concentric orbital depths.
  const cx = w / 2
  const cy = h / 2
  const sim = d3.forceSimulation<LNode>(nodes)
    .force('radial', d3.forceRadial<LNode>(d => d.radius ?? 0, cx, cy).strength(1.2))
    .force('collide', d3.forceCollide<LNode>(d => d.r + 12).iterations(3)) // 12px guaranteed padding against neighbors
    .force('charge', d3.forceManyBody<LNode>().strength(d => d.type === 'entity' ? 0 : d.type === 'aspect' ? -40 : -10))
    .stop()

  for (let i = 0; i < 120; i++) sim.tick()
  sim.stop()

  // Re-sync angle & radius from final relaxed cartesian positions for edge curved math
  nodes.forEach((n) => {
    if (n.x != null && n.y != null) {
      n.angle = Math.atan2(n.y - cy, n.x - cx) + Math.PI / 2
      n.radius = Math.sqrt((n.x - cx) ** 2 + (n.y - cy) ** 2)
    }
  })

  // Form link tuples
  rootHier.descendants().forEach((d: any) => {
    if (d.parent) {
      const parentNode = nodeById.get(d.parent.data.dataId)!
      const childNode = nodeById.get(d.data.dataId)!
      links.push({ source: parentNode, target: childNode })
    }
  })

  updateZoomBounds()
}

function zoomToFit(duration = 0) {
  if (!zoomBehavior || nodes.length === 0 || !canvasHover || w <= 0 || h <= 0)
    return

  const focalNode = nodes.find(n => n.type === 'entity')
  if (!focalNode || focalNode.x == null || focalNode.y == null)
    return

  const maxScale = 1.8

  let outX = 0
  let outY = 0
  for (const n of nodes) {
    const dx = Math.abs((n.x ?? w / 2) - focalNode.x)
    const dy = Math.abs((n.y ?? h / 2) - focalNode.y)
    if (dx > outX)
      outX = dx
    if (dy > outY)
      outY = dy
  }

  const buffer = 40
  const scaleOriginal = Math.min((w / 2 - buffer) / (outX || 1), (h / 2 - buffer) / (outY || 1))
  const scale = Math.max(0.65, Math.min(scaleOriginal * 1.05, maxScale))

  const t = d3.zoomIdentity.translate(w / 2 - focalNode.x * scale, h / 2 - focalNode.y * scale).scale(scale)
  if (duration > 0)
    d3.select(canvasHover).transition().duration(duration).call(zoomBehavior.transform as any, t)
  else
    d3.select(canvasHover).call(zoomBehavior.transform as any, t)
}

function calcArcParams(p1: { x: number, y: number }, p2: { x: number, y: number }) {
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

function drawEdges() {
  if (!_ctxEdges)
    return
  const t = currentTransform
  _ctxEdges.clearRect(0, 0, w, h)
  _ctxEdges.save()
  _ctxEdges.translate(t.x, t.y)
  _ctxEdges.scale(t.k, t.k)

  // Edge styling color: Deep Blue (#1e3a8a)
  _ctxEdges.strokeStyle = '#1e3a8a'
  _ctxEdges.globalAlpha = 0.4
  _ctxEdges.lineWidth = Math.min(0.8, 1.5 / t.k)

  for (const link of links) {
    const sx = link.source.x ?? 0
    const sy = link.source.y ?? 0
    const tx = link.target.x ?? 0
    const ty = link.target.y ?? 0

    const arc = calcArcParams({ x: sx, y: sy }, { x: tx, y: ty })

    _ctxEdges.beginPath()
    _ctxEdges.moveTo(sx, sy)
    if (arc) {
      _ctxEdges.arc(arc.cx, arc.cy, arc.r, arc.a1, arc.a2, true)
    }
    else {
      _ctxEdges.lineTo(tx, ty)
    }
    _ctxEdges.stroke()
  }

  _ctxEdges.restore()
}

function draw() {
  if (!_ctxEdges || !_ctxNodes || !_ctxHover || !_ctxHidden)
    return
  const t = currentTransform
  drawEdges()
  _ctxNodes.clearRect(0, 0, w, h)
  _ctxHover.clearRect(0, 0, w, h)
  _ctxHidden.clearRect(0, 0, w, h)

  _ctxNodes.save()
  _ctxNodes.translate(t.x, t.y)
  _ctxNodes.scale(t.k, t.k)

  for (const node of nodes) {
    _ctxNodes.beginPath()

    if (node.type === 'fact') {
      // Solid Green Diamond Facts (resembling Global Graph)
      const dr = node.r * 1.3
      const cx = node.x ?? w / 2
      const cy = node.y ?? h / 2
      _ctxNodes.moveTo(cx, cy - dr)
      _ctxNodes.lineTo(cx + dr, cy)
      _ctxNodes.lineTo(cx, cy + dr)
      _ctxNodes.lineTo(cx - dr, cy)
      _ctxNodes.closePath()
    }
    else {
      _ctxNodes.arc(node.x ?? w / 2, node.y ?? h / 2, node.r, 0, Math.PI * 2)
    }

    // Deep Blue standard border color
    _ctxNodes.strokeStyle = '#1e3a8a'
    const limitScale = Math.max(1, t.k)

    if (node.type === 'fact') {
      _ctxNodes.fillStyle = '#10b981'
      _ctxNodes.fill()
      // Facts don't have border in Global Graph usually, but if you want consistency
      // the user mentioned "Fact统一选用绿色，与Graph里形式保持一致", we fill green without stroke.
    }
    else if (node.type === 'aspect') {
      // Pure White Aspect with Blue Border, No text
      _ctxNodes.fillStyle = '#ffffff'
      _ctxNodes.globalAlpha = 1.0
      _ctxNodes.fill()

      _ctxNodes.lineWidth = Math.min(1.2, 2.0 / limitScale)
      _ctxNodes.stroke()
    }
    else if (node.type === 'entity') {
      // White Entity, Blue border
      _ctxNodes.fillStyle = '#ffffff'
      _ctxNodes.fill()

      _ctxNodes.lineWidth = Math.min(2.5, 4.0 / limitScale)
      _ctxNodes.stroke()

      // Render Text for Entity strictly
      const text = node.label.toUpperCase()
      const metrics = getEntityTextMetrics(_ctxNodes, text)
      const effR = node.r * 0.95
      let fs = Math.min(effR / metrics.radiusFactor, node.r * 0.65)
      const vis = fs * t.k
      if (vis >= 10) {
        const displayFs = Math.min(vis, 72)
        fs = displayFs / t.k
        _ctxNodes.fillStyle = '#1e293b' // Dark readable text
        _ctxNodes.font = `600 ${fs}px 'Oswald','Roboto Condensed','DIN Alternate','Arial Narrow',sans-serif`
        _ctxNodes.textAlign = 'center'
        _ctxNodes.textBaseline = 'middle'
        if (metrics.layout === 'single') {
          _ctxNodes.fillText(text, node.x ?? w / 2, node.y ?? h / 2)
        }
        else {
          _ctxNodes.fillText(metrics.line1, node.x ?? w / 2, (node.y ?? h / 2) - fs * 0.55)
          _ctxNodes.fillText(metrics.line2, node.x ?? w / 2, (node.y ?? h / 2) + fs * 0.55)
        }
      }
    }
  }
  _ctxNodes.restore()

  _ctxHidden.save()
  _ctxHidden.translate(t.x, t.y)
  _ctxHidden.scale(t.k, t.k)
  for (const node of nodes) {
    _ctxHidden.fillStyle = node.hitColor
    _ctxHidden.beginPath()
    _ctxHidden.arc(node.x ?? w / 2, node.y ?? h / 2, node.r + 4, 0, Math.PI * 2)
    _ctxHidden.fill()
  }
  _ctxHidden.restore()
}

function onMousemove(e: MouseEvent) {
  if (!_ctxHidden || !containerRef.value)
    return
  const rect = containerRef.value.getBoundingClientRect()
  const mx = Math.round(e.clientX - rect.left)
  const my = Math.round(e.clientY - rect.top)
  if (mx < 0 || my < 0 || mx >= w || my >= h)
    return
  const px = _ctxHidden.getImageData(mx, my, 1, 1).data
  const found = hitColors.lookup(px[0], px[1], px[2])
  if (!found) {
    tooltip.value = null
    return
  }
  const lines: string[] = []
  if (found.type === 'fact')
    lines.push(found.fullText || found.label)
  else if (found.fullText && found.fullText !== found.label)
    lines.push(found.fullText)

  let title = found.label
  if (found.type === 'fact')
    title = ''

  tooltip.value = { title, lines, type: found.type }
}

function onMouseleave() {
  tooltip.value = null
}

function initZoom() {
  if (!canvasHover)
    return
  d3.select(canvasHover).on('.zoom', null)
  zoomBehavior = d3.zoom<HTMLCanvasElement, unknown>()
    .scaleExtent([0.15, 6])
    .on('zoom', (event) => {
      currentTransform = event.transform
      tooltip.value = null
      draw()
    })
  d3.select(canvasHover).call(zoomBehavior)
  d3.select(canvasHover).on('dblclick.zoom', null)
}

function render() {
  if (!zoomBehavior)
    initZoom()
  buildTreeAndLayout()
  draw()
  zoomToFit()
  updateZoomBounds()
}

onMounted(() => {
  const container = containerRef.value!
  function makeCanvas(id: string, z: number, hidden = false): HTMLCanvasElement {
    const el = document.createElement('canvas')
    el.id = id
    Object.assign(el.style, {
      position: 'absolute',
      top: '0',
      left: '0',
      zIndex: String(z),
      display: hidden ? 'none' : 'block',
    })
    container.appendChild(el)
    return el
  }
  canvasEdges = makeCanvas('elg-edges', 1)
  canvasNodes = makeCanvas('elg-nodes', 2)
  canvasHover = makeCanvas('elg-hover', 3)
  canvasHidden = makeCanvas('elg-hidden', 0, true)
  _ctxEdges = canvasEdges.getContext('2d')!
  _ctxNodes = canvasNodes.getContext('2d')!
  _ctxHover = canvasHover.getContext('2d')!
  _ctxHidden = canvasHidden.getContext('2d', { willReadFrequently: true })!

  resizeAll()
  render()

  _resizeObserver = new ResizeObserver(() => {
    resizeAll()
    render()
  })
  _resizeObserver.observe(container)
  canvasHover!.addEventListener('mousemove', onMousemove)
  canvasHover!.addEventListener('mouseleave', onMouseleave)
})

onBeforeUnmount(() => {
  _resizeObserver?.disconnect()
  if (canvasHover) {
    d3.select(canvasHover).on('.zoom', null)
    canvasHover.removeEventListener('mousemove', onMousemove)
    canvasHover.removeEventListener('mouseleave', onMouseleave)
  }
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
watch(() => props.selectedEntityId, () => render())
</script>

<template>
  <div ref="containerRef" class="entity-local-graph">
    <div v-if="tooltip" class="elg-tooltip">
      <div v-if="tooltip.title" class="elg-tooltip__title">{{ tooltip.title }}</div>
      <div v-if="tooltip.lines.length" class="elg-tooltip__desc">
        <div v-for="(line, i) in tooltip.lines" :key="i">{{ line }}</div>
      </div>
      <div class="elg-tooltip__type">{{ tooltip.type }}</div>
    </div>
  </div>
</template>

<style scoped>
.entity-local-graph { position: relative; width: 100%; height: 100%; overflow: hidden; background: transparent; }
</style>

<style>
.elg-tooltip {
  position: absolute; left: 24px; bottom: 24px; z-index: 10000;
  max-width: 340px; padding: 12px 16px;
  background: rgba(255, 255, 255, 0.96); backdrop-filter: blur(10px);
  color: #334155; border-radius: 10px; border: 1px solid #e2e8f0;
  font-size: 0.8125rem; line-height: 1.5; pointer-events: none;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  animation: elg-tooltip-in 0.15s ease-out;
}
@keyframes elg-tooltip-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.elg-tooltip__title { font-weight: 600; font-size: 0.875rem; margin-bottom: 4px; color: #0f172a; }
.elg-tooltip__desc { color: #475569; word-break: break-word; }
.elg-tooltip__type { margin-top: 6px; font-size: 0.6875rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
</style>
