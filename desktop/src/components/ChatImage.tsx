import { useCallback, useEffect, useState } from 'react'
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

function needsAuthResolve(src: string): boolean {
  const s = (src || '').trim().replace(/^<|>$/g, '')
  if (!s) return false
  if (isAuthenticatedApiUrl(s)) return true
  if (isLocalMediaPath(s)) return true
  return false
}

/**
 * Chat markdown image. Local paths + loopback /api/* attachments are fetched
 * with Bearer into blob: URLs (bare img src would 401). Public https/data pass through.
 * No post-decode setState (avoids flicker).
 */
export function ChatImage({ src, alt, onOpen }: ChatImageProps) {
  const raw = (src || '').trim().replace(/^<|>$/g, '')
  const [resolved, setResolved] = useState<string | null>(() => {
    if (!raw) return null
    if (needsAuthResolve(raw)) return null
    if (isRemoteOrDataUrl(raw) && !isAuthenticatedApiUrl(raw)) return raw
    return null
  })
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

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
      <span
        className="chat-img-loading text-xs block my-1"
        style={{ color: 'var(--text-muted)' }}
      >
        Loading image…
      </span>
    )
  }

  return (
    <div
      className="chat-img-wrap group/img relative inline-block max-w-full my-1"
      style={{ overflowAnchor: 'none' }}
    >
      <button
        type="button"
        className="chat-img-btn block p-0 m-0 border-0 bg-transparent cursor-zoom-in w-full text-left"
        onClick={() => onOpen?.(resolved, alt)}
        title="Click to edit / expand"
      >
        <img
          src={resolved}
          alt={alt || 'image'}
          className="chat-img"
          // Eager: lazy-load while scrolling past images causes height jumps + sticky fight
          loading="eager"
          decoding="async"
          draggable={false}
          style={{
            maxWidth: '100%',
            maxHeight: IMG_MAX_H,
            width: 'auto',
            height: 'auto',
            objectFit: 'contain',
            borderRadius: 8,
            display: 'block',
            overflowAnchor: 'none',
          }}
        />
      </button>
      <div
        className="chat-img-actions absolute top-2 right-2 flex items-center gap-0.5 rounded-md px-0.5 py-0.5 opacity-0 group-hover/img:opacity-100 focus-within:opacity-100 transition-opacity"
        style={{
          background: 'color-mix(in srgb, var(--bg-primary) 92%, transparent)',
          border: '1px solid var(--border)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
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
