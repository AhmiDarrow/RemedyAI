import { useCallback, useEffect, useState } from 'react'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { normalizeBrowserUrl } from '../../utils/browserUrl'

const HOME = 'https://github.com/AhmiDarrow/RemedyAI'

/**
 * In-app browser: real WebView2 window (not iframe — sites that block framing work).
 * Single chrome: URL + Go / Back / Forward / Reload. Auto-opens on first visit.
 */
export function BrowserSlide() {
  const [url, setUrl] = useState(HOME)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  const navigate = useCallback(async (raw: string) => {
    const u = normalizeBrowserUrl(raw)
    if (!u) {
      setStatus('Enter an http(s) URL')
      return
    }
    setUrl(u)
    setBusy(true)
    setStatus('Loading…')
    try {
      if (!isTauri()) {
        window.open(u, '_blank', 'noopener,noreferrer')
        setStatus('Opened in browser tab (web UI)')
        return
      }
      const finalUrl = await tauriInvoke<string>('browser_navigate', { url: u })
      setUrl(finalUrl)
      setStatus('Browser window open')
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  // Auto-open home when the slide mounts (in-app browser, not external)
  useEffect(() => {
    void navigate(HOME)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const cmd = async (name: 'browser_go_back' | 'browser_go_forward' | 'browser_reload') => {
    if (!isTauri()) return
    try {
      await tauriInvoke(name)
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
          void navigate(url)
        }}
      >
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Back"
          onClick={() => void cmd('browser_go_back')}
        >
          ←
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Forward"
          onClick={() => void cmd('browser_go_forward')}
        >
          →
        </button>
        <button
          type="button"
          className="px-1.5 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
          title="Reload"
          onClick={() => void cmd('browser_reload')}
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
          style={{ background: 'var(--accent)', color: '#fff', opacity: busy ? 0.7 : 1 }}
          disabled={busy}
        >
          Go
        </button>
      </form>
      <div
        className="flex-1 min-h-0 flex flex-col items-center justify-center gap-3 p-6 text-center"
        style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
      >
        <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
          Remedy Browser
        </p>
        <p className="max-w-sm leading-relaxed">
          Pages open in an in-app WebView window (real browser engine — not an iframe). Sites that
          block embedding still work. Use the bar above to navigate.
        </p>
        <button
          type="button"
          className="px-3 py-1.5 rounded font-medium"
          style={{ background: 'var(--accent)', color: '#fff' }}
          onClick={() => void navigate(url || HOME)}
        >
          {busy ? 'Opening…' : 'Show browser'}
        </button>
        {status && (
          <p className="text-[11px] truncate max-w-full" style={{ color: 'var(--text-muted)' }}>
            {status}
          </p>
        )}
      </div>
    </div>
  )
}
