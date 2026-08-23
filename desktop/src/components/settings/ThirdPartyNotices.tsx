/**
 * In-app viewers for the binding license and the generated third-party notices.
 *
 * Vite copies ``desktop/public/`` into the bundle, so the relative fetches
 * resolve in the Tauri app, the browser WebUI, and Vite dev.
 */
import { useCallback, useState } from 'react'

/** Notices file is ~900 KB / 30k lines — render a head first, all of it on ask. */
const HEAD_LINES = 400

type Status = 'idle' | 'loading' | 'ready' | 'error'

function LegalFile({
  url,
  idleLabel,
  hideLabel,
  loadingLabel,
  missingHint,
}: {
  url: string
  idleLabel: string
  hideLabel: string
  loadingLabel: string
  missingHint: string
}) {
  const [status, setStatus] = useState<Status>('idle')
  const [text, setText] = useState('')
  const [full, setFull] = useState(false)
  const [error, setError] = useState('')

  const open = useCallback(async () => {
    if (status === 'ready') {
      setStatus('idle')
      return
    }
    setStatus('loading')
    setError('')
    try {
      const res = await fetch(url, { cache: 'no-cache' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.text()
      if (!body.trim()) throw new Error('empty')
      setText(body)
      setStatus('ready')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setStatus('error')
    }
  }, [status, url])

  const lines = text ? text.split('\n') : []
  const truncated = !full && lines.length > HEAD_LINES
  const shown = truncated ? lines.slice(0, HEAD_LINES).join('\n') : text

  return (
    <div className="mt-2 space-y-2">
      <button
        type="button"
        className="text-xs underline"
        style={{ color: 'var(--accent)', background: 'none', border: 0, padding: 0 }}
        onClick={() => void open()}
        aria-expanded={status === 'ready'}
      >
        {status === 'loading' ? loadingLabel : status === 'ready' ? hideLabel : idleLabel}
      </button>

      {status === 'error' ? (
        <p className="text-[10px]" style={{ margin: 0, color: 'var(--error)' }}>
          {missingHint} ({error}). It ships at <code>{url}</code> next to the app.
        </p>
      ) : null}

      {status === 'ready' ? (
        <div className="space-y-1.5">
          <pre
            className="text-[10px] leading-snug overflow-auto rounded-lg p-2"
            style={{
              margin: 0,
              maxHeight: 280,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
            }}
          >
            {shown}
          </pre>
          {truncated ? (
            <button
              type="button"
              className="text-[10px] underline"
              style={{ color: 'var(--accent)', background: 'none', border: 0, padding: 0 }}
              onClick={() => setFull(true)}
            >
              Show all {lines.length.toLocaleString()} lines
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function LicenseText() {
  return (
    <LegalFile
      url="LICENSE.txt"
      idleLabel="Read the license / terms →"
      hideLabel="Hide the license"
      loadingLabel="Loading license…"
      missingHint="Could not load the license"
    />
  )
}

export function ThirdPartyNotices() {
  return (
    <LegalFile
      url="THIRD_PARTY_NOTICES.txt"
      idleLabel="Third-party notices (open-source components) →"
      hideLabel="Hide third-party notices"
      loadingLabel="Loading third-party notices…"
      missingHint="Could not load the notices file"
    />
  )
}
