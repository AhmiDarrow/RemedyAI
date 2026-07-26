import { useCallback, useEffect, useState } from 'react'
import { isLocalMediaPath, resolveChatMediaUrl } from '../utils/chatMedia'

interface ChatImageProps {
  src?: string
  alt?: string
  /** Open full viewer/editor */
  onOpen?: (src: string, alt?: string) => void
  /** Attach a File (e.g. after markup) — optional parent wiring */
  onAttachMarkup?: (file: File) => void | Promise<void>
}

/**
 * Chat markdown image — rewrites local filesystem / project-relative paths
 * through the local API so previews work for every provider (not only data: URIs).
 * Hover: Edit · Copy · Save (same pattern as chat bubble actions).
 */
export function ChatImage({ src, alt, onOpen }: ChatImageProps) {
  const [resolved, setResolved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false
    setError(null)
    if (!src) {
      setResolved(null)
      return
    }
    if (!isLocalMediaPath(src)) {
      setResolved(src)
      return
    }
    // Keep prior blob while re-resolving same/new path — avoids flicker on chat reflow
    void resolveChatMediaUrl(src)
      .then((url) => {
        if (!cancelled) setResolved(url)
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          // only clear if we never had a good frame
          setResolved((prev) => prev)
        }
      })
    return () => {
      cancelled = true
    }
  }, [src])

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
      // Fallback: open in new tab
      window.open(resolved, '_blank', 'noopener,noreferrer')
    }
  }, [resolved, alt])

  if (error) {
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
        Image unavailable: {alt || src || 'file'}
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
    <div className="chat-img-wrap group/img relative inline-block max-w-full my-1">
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
          loading="lazy"
          decoding="async"
          style={{
            maxWidth: '100%',
            maxHeight: 360,
            minHeight: 48,
            objectFit: 'contain',
            borderRadius: 8,
            display: 'block',
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
