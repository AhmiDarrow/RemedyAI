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
import { shouldUseCorsForImage } from '../utils/chatMedia'
import { browserStackHold } from '../utils/browserStack'

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
 *
 * Drawing is ref + rAF — never React state per pointer-move (that stuttered
 * hard in WebView2, especially on large images).
 */
export function ImageLightbox({ src, alt, onClose, onAttachMarkup }: ImageLightboxProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [ready, setReady] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [nat, setNat] = useState({ w: 0, h: 0 })
  const [zoom, setZoom] = useState(1)
  const [tool, setTool] = useState<MarkupTool>('pen')
  const [color, setColor] = useState<string>(MARKUP_COLORS[0]!.value)
  const [width, setWidth] = useState<number>(MARKUP_WIDTHS[1]!)
  const [strokes, setStrokes] = useState<MarkupStroke[]>([])
  const [attaching, setAttaching] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [textDraft, setTextDraft] = useState<string>('')
  const [showTextPrompt, setShowTextPrompt] = useState(false)
  const textAnchorRef = useRef<Point | null>(null)

  const natRef = useRef(nat)
  const zoomRef = useRef(zoom)
  const toolRef = useRef(tool)
  const colorRef = useRef(color)
  const widthRef = useRef(width)
  const strokesRef = useRef(strokes)
  const draftRef = useRef<MarkupStroke | null>(null)
  const drawingRef = useRef(false)
  const rafRef = useRef(0)
  const stageSizeRef = useRef({ w: 800, h: 600 })
  natRef.current = nat
  zoomRef.current = zoom
  toolRef.current = tool
  colorRef.current = color
  widthRef.current = width
  strokesRef.current = strokes

  // Full-screen overlay must sit above the native Browser embed HWND.
  useEffect(() => {
    if (!src) return
    return browserStackHold('image-lightbox')
  }, [src])

  const paintNow = useCallback(() => {
    const canvas = canvasRef.current
    const img = imageRef.current
    const n = natRef.current
    if (!canvas || !img || !n.w || !n.h) return
    const { w: sw, h: sh } = stageSizeRef.current
    const availW = Math.max(40, sw - 16)
    const availH = Math.max(40, sh - 16)
    const fit = Math.min(availW / n.w, availH / n.h, 1)
    const viewScale = fit * zoomRef.current
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const cssW = Math.max(1, n.w * viewScale)
    const cssH = Math.max(1, n.h * viewScale)
    const bufW = Math.max(1, Math.round(cssW * dpr))
    const bufH = Math.max(1, Math.round(cssH * dpr))
    if (canvas.width !== bufW || canvas.height !== bufH) {
      canvas.width = bufW
      canvas.height = bufH
    }
    const cssWpx = `${cssW}px`
    const cssHpx = `${cssH}px`
    if (canvas.style.width !== cssWpx) canvas.style.width = cssWpx
    if (canvas.style.height !== cssHpx) canvas.style.height = cssHpx
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr * viewScale, 0, 0, dpr * viewScale, 0, 0)
    paintScene(ctx, img, n.w, n.h, strokesRef.current, draftRef.current)
  }, [])

  const schedulePaint = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = 0
      paintNow()
    })
  }, [paintNow])

  // Load image whenever src changes
  useEffect(() => {
    if (!src) {
      setReady(false)
      setLoadError(null)
      setStrokes([])
      draftRef.current = null
      drawingRef.current = false
      return
    }
    setReady(false)
    setLoadError(null)
    setStrokes([])
    draftRef.current = null
    drawingRef.current = false
    setZoom(1)
    setStatus(null)

    let cancelled = false
    const load = (withCors: boolean) => {
      const img = new Image()
      // blob:/data: must NOT set crossOrigin (WebView2 often fails onload).
      // http(s): use anonymous when possible so canvas markup can export.
      if (withCors && shouldUseCorsForImage(src)) {
        img.crossOrigin = 'anonymous'
      }
      img.onload = () => {
        if (cancelled) return
        imageRef.current = img
        setNat({ w: img.naturalWidth || img.width, h: img.naturalHeight || img.height })
        setReady(true)
        setLoadError(null)
      }
      img.onerror = () => {
        if (cancelled) return
        if (withCors && shouldUseCorsForImage(src)) {
          load(false)
          return
        }
        setLoadError('Could not load image')
        setReady(false)
      }
      img.src = src
    }
    load(true)
    return () => {
      cancelled = true
    }
  }, [src])

  useEffect(() => {
    if (ready) schedulePaint()
  }, [ready, nat, zoom, strokes, schedulePaint])

  useEffect(() => {
    const el = stageRef.current
    if (!el || !src) return
    const apply = () => {
      stageSizeRef.current = { w: el.clientWidth, h: el.clientHeight }
      schedulePaint()
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [src, ready, schedulePaint])

  useEffect(() => {
    return () => {
      if (rafRef.current) {
        window.cancelAnimationFrame(rafRef.current)
        rafRef.current = 0
      }
    }
  }, [])

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
        draftRef.current = null
        drawingRef.current = false
        setStrokes((s) => s.slice(0, -1))
        return
      }
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(4, z + 0.15))
      if (e.key === '-' || e.key === '_') setZoom((z) => Math.max(0.25, z - 0.15))
      if (e.key === '0') setZoom(1)
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

  const clientToImage = useCallback((clientX: number, clientY: number): Point | null => {
    const canvas = canvasRef.current
    const n = natRef.current
    if (!canvas || !n.w) return null
    const rect = canvas.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return null
    const x = ((clientX - rect.left) / rect.width) * n.w
    const y = ((clientY - rect.top) / rect.height) * n.h
    return {
      x: Math.max(0, Math.min(n.w, x)),
      y: Math.max(0, Math.min(n.h, y)),
    }
  }, [])

  const onPointerDown = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!ready || showTextPrompt) return
    const p = clientToImage(e.clientX, e.clientY)
    if (!p) return
    e.preventDefault()

    const t = toolRef.current
    if (t === 'text') {
      textAnchorRef.current = p
      setTextDraft('')
      setShowTextPrompt(true)
      return
    }

    e.currentTarget.setPointerCapture(e.pointerId)
    drawingRef.current = true
    if (t === 'pen' || t === 'highlighter') {
      draftRef.current = {
        kind: t,
        color: colorRef.current,
        width: t === 'highlighter' ? widthRef.current * 2 : widthRef.current,
        points: [p],
      }
    } else {
      draftRef.current = {
        kind: t,
        color: colorRef.current,
        width: widthRef.current,
        from: p,
        to: p,
      }
    }
    schedulePaint()
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    if (!drawingRef.current || !draftRef.current) return
    const p = clientToImage(e.clientX, e.clientY)
    if (!p) return
    const draft = draftRef.current
    if (draft.kind === 'pen' || draft.kind === 'highlighter') {
      const last = draft.points[draft.points.length - 1]
      // Skip sub-pixel jitter — fewer points, cheaper strokes.
      if (last && Math.hypot(p.x - last.x, p.y - last.y) < 1.25) return
      draft.points.push(p)
    } else if (draft.kind === 'arrow' || draft.kind === 'rect') {
      draft.to = p
    }
    schedulePaint()
  }

  const onPointerUp = (e: ReactPointerEvent<HTMLCanvasElement>) => {
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* */
    }
    if (!drawingRef.current || !draftRef.current) {
      drawingRef.current = false
      return
    }
    const end = clientToImage(e.clientX, e.clientY)
    if (end) {
      const draft = draftRef.current
      if (draft.kind === 'pen' || draft.kind === 'highlighter') {
        const last = draft.points[draft.points.length - 1]
        if (!last || last.x !== end.x || last.y !== end.y) draft.points.push(end)
      } else if (draft.kind === 'arrow' || draft.kind === 'rect') {
        draft.to = end
      }
    }
    const draft = draftRef.current
    drawingRef.current = false
    draftRef.current = null
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
    } else {
      schedulePaint()
    }
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

  const exportPngBlob = async (): Promise<Blob> => {
    const img = imageRef.current
    const n = natRef.current
    if (!img || !n.w || !n.h) throw new Error('Image not ready')
    const off = document.createElement('canvas')
    off.width = n.w
    off.height = n.h
    const ctx = off.getContext('2d')
    if (!ctx) throw new Error('Could not encode annotated image')
    paintScene(ctx, img, n.w, n.h, strokesRef.current, null)
    return canvasToPngBlob(off)
  }

  const handleAttach = async () => {
    if (!onAttachMarkup || !ready) return
    setAttaching(true)
    setStatus(null)
    try {
      const blob = await exportPngBlob()
      const file = new File(
        [blob],
        stampMarkupFilename(alt || 'image'),
        { type: 'image/png' },
      )
      await onAttachMarkup(file)
      setStatus(
        strokes.length
          ? 'Saved — attached to your next message'
          : 'Attached to your next message',
      )
      window.setTimeout(() => onClose(), 350)
    } catch (err: unknown) {
      setStatus(err instanceof Error ? err.message : 'Attach failed')
    } finally {
      setAttaching(false)
    }
  }

  const handleDownload = async () => {
    if (!ready) return
    try {
      const blob = await exportPngBlob()
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
            draftRef.current = null
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
                ? 'Saving…'
                : strokes.length
                  ? 'Save & attach to prompt'
                  : 'Attach to prompt'}
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
        ref={stageRef}
        className="flex-1 min-h-0 overflow-auto flex items-center justify-center p-4"
        onClick={(e) => e.stopPropagation()}
      >
        {loadError && (
          <div className="flex flex-col items-center gap-3">
            <div className="text-sm text-red-300">{loadError}</div>
            <img
              src={src}
              alt={alt || 'Preview'}
              className="max-w-[min(92vw,1000px)] max-h-[70vh] object-contain rounded-lg"
              style={{ border: '1px solid rgba(255,255,255,0.15)' }}
            />
          </div>
        )}
        {!ready && !loadError && (
          <div className="text-sm text-white/60">Loading image…</div>
        )}
        <canvas
          ref={canvasRef}
          className="rounded-lg shadow-2xl"
          style={{
            display: ready ? 'block' : 'none',
            maxWidth: 'none',
            cursor: tool === 'text' ? 'text' : 'crosshair',
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
