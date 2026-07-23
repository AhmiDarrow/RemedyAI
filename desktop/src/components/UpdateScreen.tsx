import { useEffect, useState } from 'react'
import type { DesktopUpdateInfo, UpdateProgress } from '../api/updates'
import { startDesktopUpdate } from '../api/updates'
import { tauriListen } from '../api/tauri'

interface UpdateScreenProps {
  info: DesktopUpdateInfo
  onClose: () => void
}

type Phase = 'ready' | 'downloading' | 'installing' | 'relaunch' | 'error'

/**
 * Full-screen update UI (Ollama-style):
 * confirm → download with progress → launch installer → app exits and relaunches.
 */
export function UpdateScreen({ info, onClose }: UpdateScreenProps) {
  const [phase, setPhase] = useState<Phase>('ready')
  const [percent, setPercent] = useState(0)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let unlisten: (() => void) | undefined
    void tauriListen('update-progress', (payload) => {
      const p = payload as UpdateProgress
      if (!p || typeof p !== 'object') return
      if (p.phase === 'downloading') {
        setPhase('downloading')
        setPercent(typeof p.percent === 'number' ? p.percent : 0)
        setMessage(p.message || 'Downloading…')
      } else if (p.phase === 'installing') {
        setPhase('installing')
        setPercent(100)
        setMessage(p.message || 'Installing…')
      } else if (p.phase === 'relaunch') {
        setPhase('relaunch')
        setMessage(p.message || 'Relaunching…')
      } else if (p.phase === 'error') {
        setPhase('error')
        setError(p.message || 'Update failed')
        setBusy(false)
      }
    }).then((fn) => {
      unlisten = fn
    })
    return () => {
      unlisten?.()
    }
  }, [])

  const begin = async () => {
    if (!info.download_url) {
      setError('No installer URL for this release.')
      setPhase('error')
      return
    }
    setBusy(true)
    setError('')
    setPhase('downloading')
    setMessage('Starting download…')
    setPercent(0)
    try {
      await startDesktopUpdate(info.download_url)
      // App should exit soon after installer launches.
    } catch (e: unknown) {
      setPhase('error')
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  const from = info.current_version
  const to = info.latest_version

  return (
    <div
      className="flex items-center justify-center h-full w-full"
      style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
    >
      <div
        className="rounded-xl shadow-2xl p-8 w-full max-w-md mx-4"
        style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border)',
        }}
      >
        <div className="text-center mb-6">
          <div className="text-2xl font-bold mb-1" style={{ color: 'var(--accent)' }}>
            Remedy Update
          </div>
          <div className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {phase === 'ready' && 'A new version is ready to install'}
            {phase === 'downloading' && 'Downloading update…'}
            {phase === 'installing' && 'Installing…'}
            {phase === 'relaunch' && 'Almost done — relaunching…'}
            {phase === 'error' && 'Update failed'}
          </div>
        </div>

        <div
          className="rounded-lg px-4 py-3 mb-5 text-sm flex justify-between items-center"
          style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
        >
          <span style={{ color: 'var(--text-muted)' }}>Version</span>
          <span className="font-medium">
            v{from} → <span style={{ color: 'var(--accent)' }}>v{to}</span>
          </span>
        </div>

        {info.release_notes && phase === 'ready' && (
          <div
            className="mb-5 text-xs max-h-28 overflow-y-auto rounded p-3"
            style={{
              background: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              whiteSpace: 'pre-wrap',
            }}
          >
            {info.release_notes.slice(0, 800)}
          </div>
        )}

        {(phase === 'downloading' || phase === 'installing' || phase === 'relaunch') && (
          <div className="mb-5">
            <div
              className="h-2 rounded-full overflow-hidden mb-2"
              style={{ background: 'var(--bg-tertiary)' }}
            >
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${Math.min(100, Math.max(0, percent))}%`,
                  background: 'var(--accent)',
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
            className="mb-5 px-3 py-2 rounded text-xs"
            style={{
              background: 'var(--error-bg, rgba(239,68,68,0.1))',
              color: 'var(--error)',
              border: '1px solid var(--error)',
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
                className="flex-1 py-2.5 rounded text-sm font-medium"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                }}
              >
                Later
              </button>
              <button
                type="button"
                onClick={() => void begin()}
                disabled={busy || !info.download_url}
                className="flex-1 py-2.5 rounded text-sm font-medium"
                style={{ background: 'var(--accent)', color: '#fff' }}
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
                className="flex-1 py-2.5 rounded text-sm font-medium"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border)',
                }}
              >
                Close
              </button>
              <button
                type="button"
                onClick={() => void begin()}
                className="flex-1 py-2.5 rounded text-sm font-medium"
                style={{ background: 'var(--accent)', color: '#fff' }}
              >
                Retry
              </button>
            </>
          )}
          {(phase === 'downloading' || phase === 'installing' || phase === 'relaunch') && (
            <div
              className="flex-1 py-2.5 text-center text-sm"
              style={{ color: 'var(--text-muted)' }}
            >
              Please wait — do not close the app
            </div>
          )}
        </div>

        <div className="mt-4 text-[0.65rem] text-center" style={{ color: 'var(--text-muted)' }}>
          Same idea as Ollama: download → install screen → app restarts updated.
        </div>
      </div>
    </div>
  )
}
