import { useCallback, useEffect, useRef, useState } from 'react'
import type { DesktopUpdateInfo, UpdateProgress } from '../api/updates'
import { startDesktopUpdate } from '../api/updates'
import { tauriListen } from '../api/tauri'
import logoSrc from '/logo.png'

interface UpdateScreenProps {
  info: DesktopUpdateInfo
  onClose: () => void
  /** When true (default), start download/install immediately - true one-click. */
  autoStart?: boolean
}

type Phase = 'ready' | 'downloading' | 'closing' | 'installing' | 'relaunch' | 'error'

/**
 * In-app update UI: download + brief "restarting" handoff.
 * Install continues after exit; keep copy calm (no triple "closing / popup" spam).
 */
export function UpdateScreen({ info, onClose, autoStart = true }: UpdateScreenProps) {
  const [phase, setPhase] = useState<Phase>(autoStart && info.download_url ? 'downloading' : 'ready')
  const [percent, setPercent] = useState(0)
  const [message, setMessage] = useState(autoStart ? 'Starting download…' : '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const startedRef = useRef(false)

  useEffect(() => {
    let unlisten: (() => void) | undefined
    void tauriListen('update-progress', (payload) => {
      const p = payload as UpdateProgress
      if (!p || typeof p !== 'object') return
      if (p.phase === 'downloading') {
        setPhase('downloading')
        setPercent(typeof p.percent === 'number' ? p.percent : 0)
        setMessage(p.message || 'Downloading…')
      } else if (p.phase === 'closing' || p.phase === 'installing' || p.phase === 'verifying') {
        // Single calm handoff phase in the UI (backend may emit several internal steps).
        setPhase('closing')
        setPercent(100)
        setMessage(p.message || 'Download complete. Restarting to finish install…')
      } else if (p.phase === 'relaunch' || p.phase === 'done') {
        setPhase('relaunch')
        setPercent(100)
        setMessage(p.message || 'Almost done…')
      } else if (p.phase === 'error') {
        setPhase('error')
        setError(p.message || 'Update failed')
        setBusy(false)
        startedRef.current = false
      }
    }).then((fn) => {
      unlisten = fn
    })
    return () => {
      unlisten?.()
    }
  }, [])

  const begin = useCallback(async () => {
    if (!info.download_url) {
      setError('No installer URL for this release.')
      setPhase('error')
      return
    }
    if (startedRef.current) return
    startedRef.current = true
    setBusy(true)
    setError('')
    setPhase('downloading')
    setMessage('Starting download...')
    setPercent(0)
    try {
      await startDesktopUpdate(info.download_url)
      // App should exit soon after the silent installer launches; POSTINSTALL relaunches.
    } catch (e: unknown) {
      setPhase('error')
      const msg = e instanceof Error ? e.message : String(e)
      setError(
        msg
          + (info.download_url
            ? `\n\nYou can install manually from:\n${info.download_url}`
            : '\n\nDownload the latest installer from GitHub Releases.'),
      )
      setBusy(false)
      startedRef.current = false
    }
  }, [info.download_url])

  // One-click: open this screen -> install starts immediately.
  useEffect(() => {
    if (autoStart && info.download_url) {
      void begin()
    }
  }, [autoStart, info.download_url, begin])

  const from = info.current_version
  const to = info.latest_version

  return (
    <div
      className="flex items-center justify-center h-full w-full"
      style={{
        background:
          'radial-gradient(90% 50% at 50% 0%, color-mix(in srgb, var(--accent) 10%, transparent), var(--bg-primary) 55%)',
        color: 'var(--text-primary)',
      }}
    >
      <div className="ui-surface w-full max-w-md mx-4 p-7">
        <div className="text-center mb-6">
          <img
            src={logoSrc}
            alt="Remedy"
            draggable={false}
            style={{
              height: 36,
              width: 'auto',
              maxWidth: 220,
              objectFit: 'contain',
              margin: '0 auto 14px',
              display: 'block',
              imageRendering: 'auto',
            }}
          />
          <div className="text-xl font-bold mb-1 tracking-tight" style={{ color: 'var(--accent)' }}>
            Remedy Update
          </div>
          <div className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {phase === 'ready' && 'A new version is ready'}
            {phase === 'downloading' && 'Downloading update…'}
            {phase === 'closing' && 'Finishing — Remedy will restart shortly'}
            {phase === 'installing' && 'Installing…'}
            {phase === 'relaunch' && 'Starting Remedy…'}
            {phase === 'error' && 'Update failed'}
          </div>
        </div>

        <div
          className="rounded-xl px-4 py-3 mb-5 text-sm flex justify-between items-center"
          style={{
            background: 'color-mix(in srgb, var(--bg-tertiary) 80%, transparent)',
            border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
          }}
        >
          <span style={{ color: 'var(--text-muted)' }}>Version</span>
          <span className="font-medium tabular-nums">
            v{from} → <span style={{ color: 'var(--accent)' }}>v{to}</span>
          </span>
        </div>

        {info.release_notes && phase === 'ready' && (
          <div
            className="mb-5 text-xs max-h-28 overflow-y-auto rounded-xl p-3"
            style={{
              background: 'color-mix(in srgb, var(--bg-primary) 80%, transparent)',
              border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
              color: 'var(--text-secondary)',
              whiteSpace: 'pre-wrap',
            }}
          >
            {info.release_notes.slice(0, 800)}
          </div>
        )}

        {(phase === 'downloading' || phase === 'closing' || phase === 'installing' || phase === 'relaunch') && (
          <div className="mb-5">
            <div
              className="h-2 rounded-full overflow-hidden mb-2"
              style={{ background: 'color-mix(in srgb, var(--bg-tertiary) 90%, transparent)' }}
            >
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${Math.min(100, Math.max(0, percent))}%`,
                  background: 'var(--accent)',
                  boxShadow: '0 0 12px color-mix(in srgb, var(--accent) 45%, transparent)',
                }}
              />
            </div>
            <div className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
              {message || `${percent}%`}
            </div>
          </div>
        )}

        {phase === 'error' && (
          <div
            className="mb-5 px-3 py-2.5 rounded-xl text-xs"
            style={{
              background: 'color-mix(in srgb, var(--error) 12%, var(--bg-secondary))',
              color: 'var(--error)',
              border: '1px solid color-mix(in srgb, var(--error) 45%, var(--border))',
            }}
          >
            {error || 'Something went wrong.'}
          </div>
        )}

        <div className="flex gap-2">
          {phase === 'ready' && (
            <>
              <button
                type="button"
                onClick={onClose}
                className="ui-btn ui-btn-secondary flex-1"
                style={{ padding: '0.65rem 0.75rem', fontSize: '0.85rem' }}
              >
                Later
              </button>
              <button
                type="button"
                onClick={() => void begin()}
                disabled={busy || !info.download_url}
                className="ui-btn ui-btn-primary flex-1"
                style={{ padding: '0.65rem 0.75rem', fontSize: '0.85rem' }}
              >
                Update & Relaunch
              </button>
            </>
          )}
          {phase === 'error' && (
            <>
              <button
                type="button"
                onClick={onClose}
                className="ui-btn ui-btn-secondary flex-1"
                style={{ padding: '0.65rem 0.75rem', fontSize: '0.85rem' }}
              >
                Close
              </button>
              <button
                type="button"
                onClick={() => void begin()}
                className="ui-btn ui-btn-primary flex-1"
                style={{ padding: '0.65rem 0.75rem', fontSize: '0.85rem' }}
              >
                Retry
              </button>
            </>
          )}
          {(phase === 'downloading' || phase === 'closing' || phase === 'relaunch') && (
            <div
              className="flex-1 py-2.5 text-center text-sm"
              style={{ color: 'var(--text-muted)' }}
            >
              {phase === 'downloading'
                ? 'Please wait…'
                : 'App restarts automatically — hang tight'}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
