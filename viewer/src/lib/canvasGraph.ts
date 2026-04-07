// viewer/src/lib/canvasGraph.ts

/** Assigns unique RGB hit-colors to canvas nodes for mouse-picking. */
export class HitColorManager<T> {
  private counter = 1
  private map = new Map<string, T>()

  nextColor(): string {
    const r = (this.counter >> 16) & 0xFF
    const g = (this.counter >> 8) & 0xFF
    const b = this.counter & 0xFF
    this.counter++
    return `rgb(${r},${g},${b})`
  }

  register(color: string, node: T): void {
    this.map.set(color, node)
  }

  lookup(r: number, g: number, b: number): T | null {
    return this.map.get(`rgb(${r},${g},${b})`) ?? null
  }

  clear(): void {
    this.counter = 1
    this.map.clear()
  }
}

/** Cached rgba() string builder — avoids repeated d3.color() calls. */
const rgbaCache = new Map<string, string>()
export function getRgba(hex: string, alpha: number): string {
  const key = `${hex}-${alpha.toFixed(2)}`
  if (rgbaCache.has(key))
    return rgbaCache.get(key)!
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (m) {
    const val = `rgba(${Number.parseInt(m[1], 16)},${Number.parseInt(m[2], 16)},${Number.parseInt(m[3], 16)},${alpha})`
    rgbaCache.set(key, val)
    return val
  }
  return `rgba(150,150,150,${alpha})`
}

/** Cached entity text-layout: picks single vs double-line, returns radiusFactor. */
const textMetricsCache = new Map<string, {
  radiusFactor: number
  layout: 'single' | 'double'
  line1: string
  line2: string
}>()

export function getEntityTextMetrics(
  ctx: CanvasRenderingContext2D,
  text: string,
) {
  if (textMetricsCache.has(text))
    return textMetricsCache.get(text)!

  ctx.save()
  ctx.font = `600 100px 'Oswald','Roboto Condensed','DIN Alternate','Arial Narrow',sans-serif`

  const wSingle = ctx.measureText(text).width / 100
  const factorSingle = Math.sqrt((wSingle / 2) ** 2 + 0.35 ** 2)

  let layout: 'single' | 'double' = 'single'
  let line1 = text
  let line2 = ''
  let radiusFactor = factorSingle

  if (text.includes(' ')) {
    const words = text.split(' ')
    const half = Math.ceil(words.length / 2)
    const l1 = words.slice(0, half).join(' ')
    const l2 = words.slice(half).join(' ')
    const w1 = ctx.measureText(l1).width / 100
    const w2 = ctx.measureText(l2).width / 100
    const factorDouble = Math.sqrt((Math.max(w1, w2) / 2) ** 2 + 0.9 ** 2)
    if (factorDouble < factorSingle) {
      radiusFactor = factorDouble
      layout = 'double'
      line1 = l1
      line2 = l2
    }
  }
  ctx.restore()
  const result = { radiusFactor, layout, line1, line2 }
  textMetricsCache.set(text, result)
  return result
}
