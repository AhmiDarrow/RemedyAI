import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import {
  MARKUP_COLORS,
  MARKUP_WIDTHS,
  canvasToPngBlob,
  paintScene,
  stampMarkupFilename,
  type MarkupStroke,
  type MarkupTool,
  type Point,
} from '../utils/imageMarkup'

interface ImageLightboxProps {
  src: string | null
  alt?: string
  onClose: () => void
  /** When set, markup can be attached to the next user prompt. */
  onAttachMarkup?: (file: File) => void | Promise<void>
}

/**
 * Full-screen image viewer for chat / Comfy outputs with Snipping-Tool-style markup.
 * Annotated images export as PNG and become prompt attachments.
 */
export function ImageLightbox({ src, alt, onClose, onAttachMarkup }: ImageLightboxProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [ready, setReady] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [nat, setNat] = useState({ w: 0, h: 0 })
  const [zoom, setZoom] = useState(1)
  const [tool, setTool] = useState<MarkupTool>('pen')
  const [color, setColor] = useState<string>(MARKUP_COLORS[0]!.value)
  const [width, setWidth] = useState<number>(MARKUP_WIDTHS[1]!)
  const [strokes, setStrokes] = useState<MarkupStroke[]>([])
  const [draft, setDraft] = useState<MarkupStroke | null>(null)
  const [drawing, setDrawing] = useState(false)
  const [attaching, setAttaching] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [textDraft, setTextDraft] = useState<string>('')
  const [showTextPrompt, setShowTextPrompt] = useState(false)
  const textAnchorRef = useRef<Point | null>(null)

  // Load image whenever src changes
  useEffect(() => {
    if (!src) {
      setReady(false)
      setLoadError(null)
      setStrokes([])
      setDraft(null)
      return
    }
    setReady(false)
    setLoadError(null)
    setStrokes([])
    setDraft(null)
    setZoom(1)
    setStatus(null)

    const img = new Image()
    // blob: and data: don't need CORS; remote may
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      imageRef.current = img
      setNat({ w: img.naturalWidth || img.width, h: img.naturalHeight || img.height })
      setReady(true)
    }
    img.onerror = () => {
      setLoadError('Could not load image')
      setReady(false)
    }
    img.src = src
  }, [src])

  const redraw = useCallback(() => {
    const canvas = canvasRef.current
    const img = imageRef.current
    if (!canvas || !img || !nat.w || !nat.h) return
    canvas.width = nat.w
    canvas.height = nat.h
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    paintScene(ctx, img, nat.w, nat.h, strokes, draft)
  }, [nat, strokes, draft])

  useEffect(() => {
    if (ready) redraw()
  }, [ready, redraw])

  useEffect(() => {
    if (!src) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showTextPrompt) {
          setShowTextPrompt(false)
          return
        }
        onClose()
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        setStrokes((s) => s.slice(0, -1))
        setDraft(null)
        return
      }
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(4, z + 0.15))
      if (e.key === '-' || e.key === '_') setZoom((z) => Math.max(0.25, z - 0.15))
      if (e.key === '0') setZoom(1)
      // Tool shortcuts
      if (!e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === 'p' || e.key === 'P') setTool('pen')
        if (e.key === 'h' || e.key === 'H') setTool('highlighter')
        if (e.key === 'a' || e.key === 'A') setTool('arrow')
        if (e.key === 'r' || e.key === 'R') setTool('rect')
        if (e.key === 't' || e.key === 'T') setTool('text')
      }
    }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [src, onClose, showTextPrompt])

  const clientToImage = useCallback(
    (clientX: number, clientY: number): Point | null => {
      const canvas = canvasRef.current
      if (!canvas || !nat.w) return null
      const rect = canvas.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return null
      const x = ((clientX - rect.left) / rect.width) * nat.w
      const y = ((clientY - rect.top) / rect.height) * nat.h
      return {
        x: Math.max(0, Math.min(nat.w, x)),
        y: Math.max(0, Math.min(nat.h, y)),
      }
    },
    [nat],
  )

  const onPointerDown = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!ready || showTextPrompt) return
    const p = clientToImage(e.clientX, e.clientY)
    if (!p) return
    e.currentTarget.setPointerCapture(e.pointerId)

    if (tool === 'text') {
      textAnchorRef.current = p
      setTextDraft('')
      setShowTextPrompt(true)
      return
    }

    setDrawing(true)
    if (tool === 'pen' || tool === 'highlighter') {
      setDraft({
        kind: tool,
        color,
        width: tool === 'highlighter' ? width * 2 : width,
        points: [p],
      })
    } else {
      setDraft({
        kind: tool,
        color,
        width,
        from: p,
        to: p,
      })
    }
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!drawing || !draft) return
    const p = clientToImage(e.clientX, e.clientY)
    if (!p) return
    if (draft.kind === 'pen' || draft.kind === 'highlighter') {
      setDraft({ ...draft, points: [...draft.points, p] })
    } else if (draft.kind === 'arrow' || draft.kind === 'rect') {
      setDraft({ ...draft, to: p })
    }
  }

  const onPointerUp = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!drawing || !draft) {
      setDrawing(false)
      return
    }
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* */
    }
    setDrawing(false)
    // Commit draft
    let meaningful = false
    if (draft.kind === 'pen' || draft.kind === 'highlighter') {
      meaningful = draft.points.length >= 1
    } else if (draft.kind === 'arrow' || draft.kind === 'rect') {
      meaningful =
        Math.hypot(draft.to.x - draft.from.x, draft.to.y - draft.from.y) > 3
    } else if (draft.kind === 'text') {
      meaningful = draft.text.trim().length > 0
    }
    if (meaningful) {
      setStrokes((s) => [...s, draft])
    }
    setDraft(null)
  }

  const commitText = () => {
    const at = textAnchorRef.current
    const text = textDraft.trim()
    setShowTextPrompt(false)
    if (!at || !text) return
    setStrokes((s) => [
      ...s,
      {
        kind: 'text',
        color,
        size: Math.max(14, Math.round(nat.w * 0.028)),
        at,
        text,
      },
    ])
    setTextDraft('')
    textAnchorRef.current = null
  }

  const handleAttach = async () => {
    if (!onAttachMarkup || !canvasRef.current || !ready) return
    setAttaching(true)
    setStatus(null)
    try {
      // Ensure latest strokes are painted
      redraw()
      // If only viewing with no strokes, still allow attach of the plain image
      // so user can re-send any chat image as attachment.
      const blob = await canvasToPngBlob(canvasRef.current)
      const file = new File([blob], stampMarkupFilename(alt), { type: 'image/png' })
      await onAttachMarkup(file)
      setStatus(
        strokes.length
          ? 'Markup attached to your next message'
          : 'Image attached to your next message',
      )
      // Brief confirmation then close so user can type the prompt
      window.setTimeout(() => onClose(), 450)
    } catch (err: unknown) {
      setStatus(err instanceof Error ? err.message : 'Attach failed')
    } finally {
      setAttaching(false)
    }
  }

  const handleDownload = async () => {
    const canvas = canvasRef.current
    if (!canvas || !ready) return
    try {
      redraw()
      const blob = await canvasToPngBlob(canvas)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = stampMarkupFilename(alt)
      a.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch {
      setStatus('Download failed')
    }
  }

  if (!src) return null

  const displayMaxW = Math.min(window.innerWidth * 0.92, 1100)
  const displayMaxH = window.innerHeight * 0.72
  const fitScale =
    nat.w && nat.h
      ? Math.min(displayMaxW / nat.w, displayMaxH / nat.h, 1)
      : 1
  const viewScale = fitScale * zoom

  const toolBtn = (id: MarkupTool, label: string, hint: string) => (
    <button
      key={id}
      type="button"
      title={hint}
      className="px-2 py-1 rounded text-xs font-medium"
      style={{
        background: tool === id ? 'var(--accent)' : 'rgba(255,255,255,0.1)',
        color: tool === id ? '#fff' : 'rgba(255,255,255,0.9)',
      }}
      onClick={() => setTool(id)}
    >
      {label}
    </button>
  )

  return (
    <div
      className="fixed inset-0 z-[100] flex flex-col"
      style={{ background: 'rgba(8,10,16,0.92)' }}
      role="dialog"
      aria-modal="true"
      aria-label="Image viewer and markup"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      {/* Toolbar */}
      <div
        className="flex flex-wrap items-center gap-2 px-3 py-2 border-b shrink-0"
        style={{
          borderColor: 'rgba(255,255,255,0.1)',
          background: 'rgba(0,0,0,0.35)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <span className="text-xs font-semibold text-white/90 mr-1 truncate max-w-[160px]">
          {alt || 'Image'}
        </span>
        <div className="flex flex-wrap gap-1">
          {toolBtn('pen', 'Pen', 'Freehand pen (P)')}
          {toolBtn('highlighter', 'Highlight', 'Highlighter (H)')}
          {toolBtn('arrow', 'Arrow', 'Arrow (A)')}
          {toolBtn('rect', 'Box', 'Rectangle (R)')}
          {toolBtn('text', 'Text', 'Text label (T)')}
        </div>

        <div className="flex items-center gap-1 ml-1">
          {MARKUP_COLORS.map((c) => (
            <button
              key={c.id}
              type="button"
              title={c.label}
              className="w-5 h-5 rounded-full border-2"
              style={{
                background: c.value,
                borderColor: color === c.value ? '#fff' : 'transparent',
              }}
              onClick={() => setColor(c.value)}
            />
          ))}
        </div>

        <div className="flex items-center gap-1">
          {MARKUP_WIDTHS.map((w) => (
            <button
              key={w}
              type="button"
              title={`Stroke ${w}px`}
              className="px-1.5 py-0.5 rounded text-[10px]"
              style={{
                background: width === w ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.08)',
                color: '#fff',
              }}
              onClick={() => setWidth(w)}
            >
              {w === 2 ? 'S' : w === 4 ? 'M' : 'L'}
            </button>
          ))}
        </div>

        <button
          type="button"
          className="px-2 py-1 rounded text-xs"
          style={{ background: 'rgba(255,255,255,0.1)', color: '#fff' }}
          title="Undo last stroke (Ctrl+Z)"
          disabled={!strokes.length}
          onClick={() => setStrokes((s) => s.slice(0, -1))}
        >
          Undo
        </button>
        <button
          type="button"
          className="px-2 py-1 rounded text-xs"
          style={{ background: 'rgba(255,255,255,0.1)', color: '#fff' }}
          disabled={!strokes.length}
          onClick={() => {
            setStrokes([])
            setDraft(null)
          }}
        >
          Clear
        </button>

        <div className="flex items-center gap-1 ml-auto">
          <button
            type="button"
            className="px-2 py-1 rounded text-xs text-white/90"
            style={{ background: 'rgba(255,255,255,0.1)' }}
            onClick={() => setZoom((z) => Math.max(0.25, z - 0.15))}
          >
            −
          </button>
          <button
            type="button"
            className="px-2 py-1 rounded text-xs text-white/80 min-w-[3rem]"
            style={{ background: 'rgba(255,255,255,0.08)' }}
            onClick={() => setZoom(1)}
            title="Reset zoom"
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            type="button"
            className="px-2 py-1 rounded text-xs text-white/90"
            style={{ background: 'rgba(255,255,255,0.1)' }}
            onClick={() => setZoom((z) => Math.min(4, z + 0.15))}
          >
            +
          </button>

          <button
            type="button"
            className="px-2 py-1 rounded text-xs text-white/90"
            style={{ background: 'rgba(255,255,255,0.1)' }}
            onClick={() => void handleDownload()}
          >
            Download
          </button>

          {onAttachMarkup && (
            <button
              type="button"
              className="px-3 py-1 rounded text-xs font-semibold"
              style={{
                background: 'var(--accent)',
                color: '#fff',
                opacity: attaching ? 0.7 : 1,
              }}
              disabled={attaching || !ready}
              title={
                strokes.length
                  ? 'Attach marked-up image to your next message'
                  : 'Attach this image to your next message'
              }
              onClick={() => void handleAttach()}
            >
              {attaching
                ? 'Attaching…'
                : strokes.length
                  ? 'Attach markup to message'
                  : 'Attach to message'}
            </button>
          )}

          <button
            type="button"
            className="px-2 py-1 rounded text-xs text-white/90"
            style={{ background: 'rgba(255,255,255,0.12)' }}
            onClick={onClose}
          >
            Close · Esc
          </button>
        </div>
      </div>

      {/* Canvas stage */}
      <div
        className="flex-1 min-h-0 overflow-auto flex items-center justify-center p-4"
        onClick={(e) => e.stopPropagation()}
      >
        {loadError && (
          <div className="text-sm text-red-300">{loadError}</div>
        )}
        {!ready && !loadError && (
          <div className="text-sm text-white/60">Loading image…</div>
        )}
        <canvas
          ref={canvasRef}
          className="rounded-lg shadow-2xl"
          style={{
            display: ready ? 'block' : 'none',
            width: nat.w ? nat.w * viewScale : undefined,
            height: nat.h ? nat.h * viewScale : undefined,
            maxWidth: 'none',
            cursor:
              tool === 'text'
                ? 'text'
                : drawing
                  ? 'crosshair'
                  : 'crosshair',
            border: '1px solid rgba(255,255,255,0.12)',
            touchAction: 'none',
          }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        />
      </div>

      {/* Footer hint */}
      <div
        className="px-3 py-1.5 text-[11px] text-white/55 border-t shrink-0 flex items-center justify-between gap-2"
        style={{ borderColor: 'rgba(255,255,255,0.08)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <span>
          Markup · Pen / Highlight / Arrow / Box / Text · Ctrl+Z undo · Zoom −/+ / 0
          {onAttachMarkup
            ? ' · Attach markup sends the annotated image with your next prompt'
            : ''}
        </span>
        {status && (
          <span className="font-medium" style={{ color: 'var(--accent)' }}>
            {status}
          </span>
        )}
      </div>

      {/* Text input overlay */}
      {showTextPrompt && (
        <div
          className="absolute inset-0 z-[110] flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.45)' }}
          onClick={() => setShowTextPrompt(false)}
        >
          <div
            className="rounded-xl p-4 w-[min(90vw,360px)] shadow-xl"
            style={{
              background: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-sm font-semibold mb-2">Add text label</div>
            <input
              autoFocus
              className="w-full px-2 py-1.5 rounded text-sm mb-3"
              style={{
                background: 'var(--bg-secondary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
              placeholder="e.g. this button is wrong"
              value={textDraft}
              onChange={(e) => setTextDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  commitText()
                }
                if (e.key === 'Escape') setShowTextPrompt(false)
              }}
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="px-3 py-1 rounded text-xs"
                style={{ color: 'var(--text-muted)' }}
                onClick={() => setShowTextPrompt(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="px-3 py-1 rounded text-xs font-semibold"
                style={{ background: 'var(--accent)', color: '#fff' }}
                onClick={commitText}
              >
                Place text
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
