import { useEffect, useState } from 'react'
import { isLocalMediaPath, resolveChatMediaUrl } from '../utils/chatMedia'

interface ChatImageProps {
  src?: string
  alt?: string
  onOpen?: (src: string, alt?: string) => void
}

/**
 * Chat markdown image — rewrites local filesystem / project-relative paths
 * through the local API so previews work for every provider (not only data: URIs).
 */
export function ChatImage({ src, alt, onOpen }: ChatImageProps) {
  const [resolved, setResolved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

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
    setResolved(null)
    void resolveChatMediaUrl(src)
      .then((url) => {
        if (!cancelled) setResolved(url)
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e))
          setResolved(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [src])

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
    <button
      type="button"
      className="chat-img-btn block p-0 m-0 border-0 bg-transparent cursor-zoom-in w-full text-left"
      onClick={() => onOpen?.(resolved, alt)}
      title="Click to expand"
    >
      <img
        src={resolved}
        alt={alt || 'image'}
        className="chat-img"
        loading="lazy"
      />
    </button>
  )
}
