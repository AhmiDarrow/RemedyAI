import { useCallback, useState } from 'react'
import { isTauri, tauriInvoke } from '../../api/tauri'

const HOME = 'https://github.com/AhmiDarrow/RemedyAI'

/**
 * In-panel browser uses a sandboxed iframe (many sites block framing).
 * "Open in Firefox / default browser" uses a real external browser via Rust.
 */
export function BrowserSlide() {
  const [url, setUrl] = useState(HOME)
  const [active, setActive] = useState(HOME)
  const [frameBlocked, setFrameBlocked] = useState(false)
  const [status, setStatus] = useState('')

  const normalize = (raw: string) => {
    let u = raw.trim()
    if (!u) return ''
    if (!/^https?:\/\//i.test(u) && !u.startsWith('about:')) u = `https://${u}`
    return u
  }

  const go = (raw: string) => {
    const u = normalize(raw)
    if (!u) return
    setUrl(u)
    setActive(u)
    setFrameBlocked(false)
    setStatus('')
  }

  const openExternal = useCallback(
    async (preferFirefox: boolean) => {
      const u = normalize(url) || active
      if (!u) return
      setStatus(preferFirefox ? 'Opening Firefox…' : 'Opening browser…')
      try {
        if (isTauri()) {
          const msg = await tauriInvoke<string>('open_external_url', {
            url: u,
            preferFirefox,
          })
          setStatus(msg)
        } else {
          window.open(u, '_blank', 'noopener,noreferrer')
          setStatus('Opened in a new tab')
        }
      } catch (e: unknown) {
        setStatus(e instanceof Error ? e.message : String(e))
      }
    },
    [url, active],
  )

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
      </form>
      <div
        className="flex flex-wrap gap-1 px-2 py-1 border-b shrink-0"
        style={{ borderColor: 'var(--border)' }}
      >
        <button
          type="button"
          className="px-2 py-0.5 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          onClick={() => void openExternal(true)}
          title="Prefer Firefox when installed"
        >
          Open in Firefox
        </button>
        <button
          type="button"
          className="px-2 py-0.5 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          onClick={() => void openExternal(false)}
        >
          Default browser
        </button>
        {status && (
          <span className="truncate py-0.5" style={{ color: 'var(--text-muted)' }}>
            {status}
          </span>
        )}
      </div>
      <div className="relative flex-1 min-h-0">
        <iframe
          title="Remedy browser"
          src={active}
          className="absolute inset-0 w-full h-full border-0"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
          referrerPolicy="no-referrer"
          onLoad={() => setFrameBlocked(false)}
          onError={() => setFrameBlocked(true)}
        />
        {frameBlocked && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center"
            style={{ background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}
          >
            <p>This site blocks in-app framing.</p>
            <button
              type="button"
              className="px-3 py-1.5 rounded font-medium"
              style={{ background: 'var(--accent)', color: '#fff' }}
              onClick={() => void openExternal(true)}
            >
              Open in Firefox
            </button>
          </div>
        )}
      </div>
      <div
        className="px-2 py-1 text-[10px] shrink-0 border-t"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        In-app view is sandboxed. Prefer Firefox/external for full sites.
      </div>
    </div>
  )
}
