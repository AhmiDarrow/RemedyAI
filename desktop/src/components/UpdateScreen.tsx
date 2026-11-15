import { useCallback, useEffect, useRef, useState } from 'react'
import type { DesktopUpdateInfo, UpdateProgress } from '../api/updates'
import { startDesktopUpdate } from '../api/updates'
import { tauriListen } from '../api/tauri'
import { useI18n } from '../i18n'
import logoSrc from '/logo.png'

type TFn = (key: string, vars?: Record<string, string>) => string

/** Map known Rust progress lines onto catalog keys. Unknown text stays as-is. */
function localizeBackendMessage(raw: string, t: TFn): string {
  const msg = (raw || '').trim()
  if (!msg) return ''
  const downloading = /^Downloading update\.\.\.\s+([0-9]+(?:\.[0-9]+)?)\s+MB\s*$/i.exec(msg)
  if (downloading) return t('update.downloadingMb', { mb: downloading[1] })
  if (msg.startsWith('Download complete')) return t('update.downloadComplete')
  return raw
}

interface UpdateScreenProps {
  info: DesktopUpdateInfo
  onClose: () => void
  /** When true (default), start download/install immediately - true one-click. */
  autoStart?: boolean
}

type Phase = 'ready' | 'downloading' | 'closing' | 'installing' | 'relaunch' | 'error'

/**
 * In-app update UI: download + brief "restarting" handoff.
 * Visual language matches SetupWizard (remedy-shell tokens).
 */
export function UpdateScreen({ info, onClose, autoStart = true }: UpdateScreenProps) {
  const { t } = useI18n()
  const [phase, setPhase] = useState<Phase>(autoStart && info.download_url ? 'downloading' : 'ready')
  const [percent, setPercent] = useState(0)
  const [message, setMessage] = useState(autoStart ? t('update.startingDownload') : '')
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
        setMessage(localizeBackendMessage(p.message || '', t) || t('update.downloadingShort'))
      } else if (p.phase === 'closing' || p.phase === 'installing' || p.phase === 'verifying') {
        // Single calm handoff phase in the UI (backend may emit several internal steps).
        setPhase('closing')
        setPercent(100)
        setMessage(localizeBackendMessage(p.message || '', t) || t('update.downloadComplete'))
      } else if (p.phase === 'relaunch' || p.phase === 'done') {
        setPhase('relaunch')
        setPercent(100)
        setMessage(localizeBackendMessage(p.message || '', t) || t('update.almost'))
      } else if (p.phase === 'error') {
        setPhase('error')
        setError(p.message || t('update.failed'))
        setBusy(false)
        startedRef.current = false
      }
    }).then((fn) => {
      unlisten = fn
    })
    return () => {
      unlisten?.()
    }
  }, [t])

  const begin = useCallback(async () => {
    if (!info.download_url) {
      setError(t('update.noUrl'))
      setPhase('error')
      return
    }
    if (startedRef.current) return
    startedRef.current = true
    setBusy(true)
    setError('')
    setPhase('downloading')
    setMessage(t('update.startingDownload'))
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
            ? `\n\n${t('update.manual')}\n${info.download_url}`
            : `\n\n${t('update.github')}`),
      )
      setBusy(false)
      startedRef.current = false
    }
  }, [info.download_url, t])

  // One-click: open this screen -> install starts immediately.
  useEffect(() => {
    if (autoStart && info.download_url) {
      void begin()
    }
  }, [autoStart, info.download_url, begin])

  const from = info.current_version
  const to = info.latest_version

  const subtitle =
    phase === 'ready'
      ? t('update.ready')
      : phase === 'downloading'
        ? t('update.downloading')
        : phase === 'closing' || phase === 'installing'
          ? t('update.finishing')
          : phase === 'relaunch'
            ? t('update.startingApp')
            : t('update.failed')

  return (
    <div className="remedy-shell">
      <div className="remedy-shell-card" style={{ maxWidth: 420, padding: '1.75rem 1.75rem 1.5rem' }}>
        <div className="text-center mb-5">
          <img src={logoSrc} alt="Remedy" className="remedy-shell-logo" draggable={false} />
          <h1 className="remedy-shell-title remedy-shell-title--accent">{t('update.title')}</h1>
          <p className="remedy-shell-subtitle">{subtitle}</p>
        </div>

        <div className="remedy-shell-meta mb-5">
          <span className="remedy-shell-muted">{t('update.version')}</span>
          <span className="font-medium tabular-nums" style={{ color: 'var(--text-primary)' }}>
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
            <div className="remedy-shell-progress-track mb-2" style={{ height: '0.5rem' }}>
              <div
                className="remedy-shell-progress-fill"
                style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
              />
            </div>
            <div className="text-xs text-center remedy-shell-muted">
              {message || `${percent}%`}
            </div>
          </div>
        )}

        {phase === 'error' && (
          <div className="remedy-shell-error mb-5">
            {error || t('update.wrong')}
          </div>
        )}

        <div className="remedy-shell-actions">
          {phase === 'ready' && (
            <>
              <button
                type="button"
                onClick={onClose}
                className="ui-btn ui-btn-secondary"
              >
                {t('update.later')}
              </button>
              <button
                type="button"
                onClick={() => void begin()}
                disabled={busy || !info.download_url}
                className="ui-btn ui-btn-primary"
              >
                {t('update.relaunch')}
              </button>
            </>
          )}
          {phase === 'error' && (
            <>
              <button
                type="button"
                onClick={onClose}
                className="ui-btn ui-btn-secondary"
              >
                {t('win.close')}
              </button>
              <button
                type="button"
                onClick={() => void begin()}
                className="ui-btn ui-btn-primary"
              >
                {t('update.retry')}
              </button>
            </>
          )}
          {(phase === 'downloading' || phase === 'closing' || phase === 'relaunch') && (
            <div
              className="flex-1 py-2.5 text-center text-sm remedy-shell-muted"
            >
              {phase === 'downloading'
                ? t('update.pleaseWait')
                : t('update.hangTight')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
