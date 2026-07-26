/** Lightweight Snipping-Tool-style markup model for chat images. */

export type MarkupTool = 'pen' | 'highlighter' | 'arrow' | 'rect' | 'text'

export type Point = { x: number; y: number }

export type MarkupStroke =
  | {
      kind: 'pen' | 'highlighter'
      color: string
      width: number
      points: Point[]
    }
  | {
      kind: 'arrow' | 'rect'
      color: string
      width: number
      from: Point
      to: Point
    }
  | {
      kind: 'text'
      color: string
      size: number
      at: Point
      text: string
    }

export const MARKUP_COLORS = [
  { id: 'red', value: '#ef4444', label: 'Red' },
  { id: 'orange', value: '#f97316', label: 'Orange' },
  { id: 'yellow', value: '#eab308', label: 'Yellow' },
  { id: 'green', value: '#22c55e', label: 'Green' },
  { id: 'blue', value: '#3b82f6', label: 'Blue' },
  { id: 'white', value: '#f8fafc', label: 'White' },
  { id: 'black', value: '#0f172a', label: 'Black' },
] as const

export const MARKUP_WIDTHS = [2, 4, 8] as const

function drawArrow(
  ctx: CanvasRenderingContext2D,
  from: Point,
  to: Point,
  color: string,
  width: number,
) {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const head = Math.max(10, width * 3.5)

  ctx.save()
  ctx.strokeStyle = color
  ctx.fillStyle = color
  ctx.lineWidth = width
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  ctx.moveTo(from.x, from.y)
  ctx.lineTo(to.x, to.y)
  ctx.stroke()

  // Arrow head
  const left = {
    x: to.x - ux * head - uy * head * 0.45,
    y: to.y - uy * head + ux * head * 0.45,
  }
  const right = {
    x: to.x - ux * head + uy * head * 0.45,
    y: to.y - uy * head - ux * head * 0.45,
  }
  ctx.beginPath()
  ctx.moveTo(to.x, to.y)
  ctx.lineTo(left.x, left.y)
  ctx.lineTo(right.x, right.y)
  ctx.closePath()
  ctx.fill()
  ctx.restore()
}

export function paintStroke(ctx: CanvasRenderingContext2D, stroke: MarkupStroke): void {
  if (stroke.kind === 'pen' || stroke.kind === 'highlighter') {
    if (stroke.points.length < 2) {
      const p = stroke.points[0]
      if (!p) return
      ctx.save()
      ctx.fillStyle = stroke.color
      if (stroke.kind === 'highlighter') ctx.globalAlpha = 0.35
      ctx.beginPath()
      ctx.arc(p.x, p.y, stroke.width / 2, 0, Math.PI * 2)
      ctx.fill()
      ctx.restore()
      return
    }
    ctx.save()
    ctx.strokeStyle = stroke.color
    ctx.lineWidth = stroke.width
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    if (stroke.kind === 'highlighter') {
      ctx.globalAlpha = 0.35
      ctx.lineWidth = stroke.width * 2.2
    }
    ctx.beginPath()
    ctx.moveTo(stroke.points[0]!.x, stroke.points[0]!.y)
    for (let i = 1; i < stroke.points.length; i++) {
      const p = stroke.points[i]!
      ctx.lineTo(p.x, p.y)
    }
    ctx.stroke()
    ctx.restore()
    return
  }

  if (stroke.kind === 'rect') {
    const x = Math.min(stroke.from.x, stroke.to.x)
    const y = Math.min(stroke.from.y, stroke.to.y)
    const w = Math.abs(stroke.to.x - stroke.from.x)
    const h = Math.abs(stroke.to.y - stroke.from.y)
    ctx.save()
    ctx.strokeStyle = stroke.color
    ctx.lineWidth = stroke.width
    ctx.strokeRect(x, y, w, h)
    ctx.restore()
    return
  }

  if (stroke.kind === 'arrow') {
    drawArrow(ctx, stroke.from, stroke.to, stroke.color, stroke.width)
    return
  }

  if (stroke.kind === 'text' && stroke.text.trim()) {
    ctx.save()
    ctx.fillStyle = stroke.color
    ctx.font = `600 ${stroke.size}px Inter, system-ui, sans-serif`
    ctx.textBaseline = 'top'
    // Soft outline for readability on any background
    ctx.lineWidth = Math.max(2, stroke.size / 8)
    ctx.strokeStyle = 'rgba(0,0,0,0.55)'
    ctx.strokeText(stroke.text, stroke.at.x, stroke.at.y)
    ctx.fillText(stroke.text, stroke.at.x, stroke.at.y)
    ctx.restore()
  }
}

/** Paint base image + all markup strokes into `ctx` (image pixel space). */
export function paintScene(
  ctx: CanvasRenderingContext2D,
  image: CanvasImageSource,
  naturalW: number,
  naturalH: number,
  strokes: MarkupStroke[],
  draft?: MarkupStroke | null,
): void {
  ctx.clearRect(0, 0, naturalW, naturalH)
  ctx.drawImage(image, 0, 0, naturalW, naturalH)
  for (const s of strokes) paintStroke(ctx, s)
  if (draft) paintStroke(ctx, draft)
}

export function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob)
        else reject(new Error('Could not encode annotated image'))
      },
      'image/png',
      0.92,
    )
  })
}

export function stampMarkupFilename(alt?: string): string {
  const base = (alt || 'image')
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/[^\w.-]+/g, '_')
    .slice(0, 40) || 'image'
  const ts = new Date().toISOString().replace(/[:.]/g, '').slice(0, 15)
  return `${base}-markup-${ts}.png`
}
