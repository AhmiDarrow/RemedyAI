import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import {
  isAuthenticatedApiUrl,
  isLocalMediaPath,
  isRemoteOrDataUrl,
  resolveChatMediaUrl,
} from '../utils/chatMedia'

interface ChatImageProps {
  src?: string
  alt?: string
  onOpen?: (src: string, alt?: string) => void
  onAttachMarkup?: (file: File) => void | Promise<void>
}

const IMG_MAX_H = 360
/** Placeholder height while decoding — reduces sticky-scroll fights. */
const IMG_SLOT_H = 180
/** After scroll settles this long, paint images again. */
const SCROLL_SETTLE_MS = 140

function needsAuthResolve(src: string): boolean {
  const s = (src || '').trim().replace(/^<|>$/g, '')
  if (!s) return false
  if (isAuthenticatedApiUrl(s)) return true
  if (isLocalMediaPath(s)) return true
  return false
}

/**
 * While the chat scroller moves, hide the *bitmap* (visibility:hidden) but keep the
 * reserved slot — avoids paint thrash / sticky reflow without collapsing layout.
 * Images start reserved + deferred until first paint after settle.
 */
function useScrollBitmapDefer(wrapRef: RefObject<HTMLElement | null>) {
  const [deferPaint, setDeferPaint] = useState(true)
  const settleTimer = useRef<number | null>(null)

  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return

    let scroller: HTMLElement | null = wrap.parentElement
    while (scroller) {
      const st = getComputedStyle(scroller)
      const oy = st.overflowY
      if (
        (oy === 'auto' || oy === 'scroll' || oy === 'overlay')
        && scroller.scrollHeight > scroller.clientHeight + 8
      ) {
        break
      }
      scroller = scroller.parentElement
    }
    if (!scroller) {
      // No scroller yet — allow paint after a frame
      const t = window.setTimeout(() => setDeferPaint(false), 48)
      return () => window.clearTimeout(t)
    }

    const markMoving = () => {
      setDeferPaint(true)
      if (settleTimer.current != null) window.clearTimeout(settleTimer.current)
      settleTimer.current = window.setTimeout(() => {
        settleTimer.current = null
        setDeferPaint(false)
      }, SCROLL_SETTLE_MS)
    }

    // Initial: wait one settle window so first decode doesn't fight stick-to-bottom
    settleTimer.current = window.setTimeout(() => {
      settleTimer.current = null
      setDeferPaint(false)
    }, SCROLL_SETTLE_MS)

    scroller.addEventListener('scroll', markMoving, { passive: true })
    scroller.addEventListener('wheel', markMoving, { passive: true })
    scroller.addEventListener('touchmove', markMoving, { passive: true })
    return () => {
      scroller.removeEventListener('scroll', markMoving)
      scroller.removeEventListener('wheel', markMoving)
      scroller.removeEventListener('touchmove', markMoving)
      if (settleTimer.current != null) window.clearTimeout(settleTimer.current)
    }
  }, [wrapRef])

  return deferPaint
}

/**
 * Chat markdown image. Local paths + loopback /api/* attachments are fetched
 * with Bearer into blob: URLs (bare img src would 401). Public https/data pass through.
 *
 * Smoothness: reserved slot + hide bitmap during scroll (not display:none).
 */
export function ChatImage({ src, alt, onOpen }: ChatImageProps) {
  const raw = (src || '').trim().replace(/^<|>$/g, '')
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const deferPaint = useScrollBitmapDefer(wrapRef)
  const [resolved, setResolved] = useState<string | null>(() => {
    if (!raw) return null
    if (needsAuthResolve(raw)) return null
    if (isRemoteOrDataUrl(raw) && !isAuthenticatedApiUrl(raw)) return raw
    return null
  })
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [slotH, setSlotH] = useState(IMG_SLOT_H)

  useEffect(() => {
    let cancelled = false
    setError(null)
    if (!raw) {
      setResolved(null)
      return
    }

    // Public http(s)/data/blob (not loopback API) — direct paint
    if (!needsAuthResolve(raw) && isRemoteOrDataUrl(raw)) {
      setResolved(raw)
      return
    }

    if (!needsAuthResolve(raw)) {
      setResolved(null)
      setError('Unsupported image URL')
      return
    }

    void resolveChatMediaUrl(raw)
      .then((url) => {
        if (!cancelled && url) setResolved(url)
        else if (!cancelled && !url) setError('Could not resolve image')
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
        }
      })
    return () => {
      cancelled = true
    }
  }, [raw])

  const handleCopy = useCallback(async () => {
    if (!resolved) return
    try {
      const res = await fetch(resolved)
      const blob = await res.blob()
      if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
        await navigator.clipboard.write([
          new ClipboardItem({ [blob.type || 'image/png']: blob }),
        ])
      } else {
        await navigator.clipboard.writeText(resolved)
      }
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      try {
        await navigator.clipboard.writeText(resolved)
        setCopied(true)
        window.setTimeout(() => setCopied(false), 1200)
      } catch {
        /* ignore */
      }
    }
  }, [resolved])

  const handleSave = useCallback(async () => {
    if (!resolved) return
    try {
      const res = await fetch(resolved)
      const blob = await res.blob()
      const ext =
        blob.type.includes('jpeg') || blob.type.includes('jpg')
          ? 'jpg'
          : blob.type.includes('webp')
            ? 'webp'
            : blob.type.includes('gif')
              ? 'gif'
              : 'png'
      const name = (alt || 'image').replace(/[^\w.-]+/g, '_').slice(0, 48) || 'image'
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${name}.${ext}`
      a.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 2000)
    } catch {
      window.open(resolved, '_blank', 'noopener,noreferrer')
    }
  }, [resolved, alt])

  if (error && !resolved) {
    return (
      <span
        className="chat-img-error text-xs block my-1 px-2 py-1 rounded"
        style={{
          color: 'var(--warning)',
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border)',
        }}
        title={error}
      >
        Image unavailable: {alt || raw || 'file'}
      </span>
    )
  }

  if (!resolved) {
    return (
      <div
        ref={wrapRef}
        className="chat-img-loading my-1 rounded-lg"
        style={{
          minHeight: IMG_SLOT_H,
          maxHeight: IMG_MAX_H,
          width: '100%',
          maxWidth: '100%',
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border)',
          contentVisibility: 'auto',
          containIntrinsicSize: `auto ${IMG_SLOT_H}px`,
          overflowAnchor: 'none',
        }}
        aria-busy="true"
      >
        <span className="text-xs block px-2 py-2" style={{ color: 'var(--text-muted)' }}>
          Loading image…
        </span>
      </div>
    )
  }

  // Hide bitmap while scrolling; keep reserved box so layout does not thrash.
  const hideBitmap = deferPaint

  return (
    <div
      ref={wrapRef}
      className="chat-img-wrap group/img relative inline-block max-w-full my-1"
      style={{
        overflowAnchor: 'none',
        minHeight: Math.min(slotH, IMG_MAX_H),
        maxHeight: IMG_MAX_H,
        contentVisibility: 'auto',
        containIntrinsicSize: `auto ${Math.min(slotH, IMG_MAX_H)}px`,
      }}
    >
      <button
        type="button"
        className="chat-img-btn block p-0 m-0 border-0 bg-transparent cursor-zoom-in w-full text-left"
        onClick={() => onOpen?.(resolved, alt)}
        title="Click to edit / expand"
        style={{ minHeight: Math.min(slotH, IMG_MAX_H) }}
      >
        <img
          src={resolved}
          alt={alt || 'image'}
          className="chat-img"
          // Eager fetch; paint is gated by visibility during scroll
          loading="eager"
          decoding="async"
          draggable={false}
          onLoad={(e) => {
            const el = e.currentTarget
            if (el.naturalHeight > 0) {
              const w = el.clientWidth || el.naturalWidth
              const h = Math.round((el.naturalHeight / el.naturalWidth) * w)
              if (h > 0) setSlotH(Math.min(Math.max(h, 48), IMG_MAX_H))
            }
          }}
          style={{
            maxWidth: '100%',
            maxHeight: IMG_MAX_H,
            width: 'auto',
            height: 'auto',
            objectFit: 'contain',
            borderRadius: 8,
            display: 'block',
            overflowAnchor: 'none',
            // Keep layout; only skip painting during scroll
            visibility: hideBitmap ? 'hidden' : 'visible',
          }}
        />
      </button>
      <div
        className="chat-img-actions absolute top-2 right-2 flex items-center gap-0.5 rounded-md px-0.5 py-0.5 opacity-0 group-hover/img:opacity-100 focus-within:opacity-100 transition-opacity"
        style={{
          background: 'color-mix(in srgb, var(--bg-primary) 92%, transparent)',
          border: '1px solid var(--border)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
          // Don't show chrome while bitmap is deferred
          pointerEvents: hideBitmap ? 'none' : undefined,
          visibility: hideBitmap ? 'hidden' : undefined,
        }}
      >
        <button
          type="button"
          className="px-1.5 py-0.5 rounded text-[10px] font-medium"
          style={{ color: 'var(--text-primary)' }}
          title="Edit / markup"
          onClick={(e) => {
            e.stopPropagation()
            onOpen?.(resolved, alt)
          }}
        >
          Edit
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded text-[10px] font-medium"
          style={{ color: copied ? 'var(--accent)' : 'var(--text-primary)' }}
          title="Copy image"
          onClick={(e) => {
            e.stopPropagation()
            void handleCopy()
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded text-[10px] font-medium"
          style={{ color: 'var(--text-primary)' }}
          title="Save image"
          onClick={(e) => {
            e.stopPropagation()
            void handleSave()
          }}
        >
          Save
        </button>
      </div>
    </div>
  )
}
