import { useCallback, useEffect, useState } from 'react'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { normalizeBrowserUrl } from '../../utils/browserUrl'
import { openExternalUrl } from '../../api/auth'

const HOME = 'https://github.com/AhmiDarrow/RemedyAI'

/**
 * Embedded browser panel: iframe in the slide (not a separate popup window).
 * Sites that block framing show a friendly note + Open externally.
 */
export function BrowserSlide() {
  const [url, setUrl] = useState(HOME)
  const [activeUrl, setActiveUrl] = useState(HOME)
  const [status, setStatus] = useState('')
  const [frameKey, setFrameKey] = useState(0)
  const [frameError, setFrameError] = useState(false)

  const go = useCallback((raw: string) => {
    const u = normalizeBrowserUrl(raw)
    if (!u) {
      setStatus('Enter an http(s) URL')
      return
    }
    setUrl(u)
    setActiveUrl(u)
    setFrameError(false)
    setFrameKey((k) => k + 1)
    setStatus('')
  }, [])

  // Load home once when the slide mounts
  useEffect(() => {
    go(HOME)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openExternal = async () => {
    const u = normalizeBrowserUrl(url) || activeUrl
    try {
      if (isTauri()) {
        try {
          await openExternalUrl(u)
          setStatus('Opened in system browser')
          return
        } catch {
          await tauriInvoke('open_external_url', { url: u })
          setStatus('Opened in system browser')
          return
        }
      }
      window.open(u, '_blank', 'noopener,noreferrer')
      setStatus('Opened in browser tab')
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs">
      <form
        className="flex gap-1 px-2 py-1.5 border-b shrink-0"
        style={{ borderColor: 'var(--border)' }}
        onSubmit={(e) => {
          e.preventDefault()
          go(url)
        }}
      >
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Home"
          onClick={() => go(HOME)}
        >
          ⌂
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Reload"
          onClick={() => {
            setFrameError(false)
            setFrameKey((k) => k + 1)
          }}
        >
          ↻
        </button>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="flex-1 min-w-0 rounded px-1.5 py-1 outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          placeholder="https://"
          aria-label="Browser URL"
          spellCheck={false}
        />
        <button
          type="submit"
          className="px-2 py-1 rounded font-medium"
          style={{ background: 'var(--accent)', color: '#fff' }}
        >
          Go
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Open in system browser"
          onClick={() => void openExternal()}
        >
          ↗
        </button>
      </form>

      <div className="flex-1 min-h-0 relative" style={{ background: '#fff' }}>
        {frameError ? (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center"
            style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
          >
            <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
              This site blocked embedding
            </p>
            <p className="max-w-sm leading-relaxed text-[11px]">
              Some sites refuse to load inside a panel. Open them in your system browser instead.
            </p>
            <button
              type="button"
              className="px-3 py-1.5 rounded font-medium"
              style={{ background: 'var(--accent)', color: '#fff' }}
              onClick={() => void openExternal()}
            >
              Open externally
            </button>
          </div>
        ) : (
          <iframe
            key={frameKey}
            title="Remedy Browser"
            src={activeUrl}
            className="absolute inset-0 w-full h-full border-0"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
            referrerPolicy="no-referrer-when-downgrade"
            onError={() => setFrameError(true)}
          />
        )}
      </div>

      {status && (
        <div
          className="px-2 py-1 border-t truncate shrink-0"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
          title={status}
        >
          {status}
        </div>
      )}
    </div>
  )
}
