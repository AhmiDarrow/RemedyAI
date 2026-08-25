import { getServerUrl } from '../api/client'
import { useEffect, useState } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'
import { isLinuxDesktop } from '../utils/platform'
import { useI18n } from '../i18n'

export interface QuitServerWarningProps {
  open: boolean
  onCancel: () => void
  /** Called after user confirms quit (and optional “don't show again”). */
  onConfirmQuit: (dontWarnAgain: boolean) => void
}

/**
 * Warn that full quit stops the local API (and browser WebUI).
 * Prefer Switch to WebUI / tray hide to keep the server running.
 */
export function QuitServerWarning({ open, onCancel, onConfirmQuit }: QuitServerWarningProps) {
  const { t } = useI18n()
  const [dontWarn, setDontWarn] = useState(false)

  useEffect(() => {
    if (open) setDontWarn(false)
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[220] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="quit-warn-title"
      onClick={onCancel}
    >
      <div
        className="ui-surface w-full max-w-md p-5"
        style={{ color: 'var(--text-primary)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          id="quit-warn-title"
          className="text-sm font-semibold mb-2 tracking-tight"
          style={{ color: 'var(--error)' }}
        >
          {t('quit.title')}
        </div>
        <div className="text-xs leading-relaxed mb-3 space-y-2" style={{ color: 'var(--text-secondary)' }}>
          <p>
            {t('quit.body1')}{' '}
            <code
              className="px-1 py-0.5 rounded text-[0.7rem]"
              style={{
                color: 'var(--accent)',
                background: 'color-mix(in srgb, var(--accent) 10%, var(--bg-tertiary))',
              }}
            >
              {getServerUrl().replace(/^https?:\/\//, '')}
            </code>
          </p>
          <p>
            {t('quit.body2')}
            {isLinuxDesktop()
              ? ' — that minimizes the desktop and leaves the server running.'
              : ' — that hides the desktop to the tray and leaves the server running.'}
          </p>
          <p style={{ color: 'var(--text-muted)' }}>
            {isLinuxDesktop()
              ? 'Window ✕ minimizes to the taskbar. Quit from this dialog or the app menu for a full stop.'
              : 'Window ✕ always hides to tray. Use tray Quit for a full stop.'}
          </p>
        </div>
        <label className="flex items-start gap-2 mb-4 cursor-pointer text-xs">
          <input
            type="checkbox"
            checked={dontWarn}
            onChange={(e) => setDontWarn(e.target.checked)}
            className="mt-0.5"
            style={{ accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--text-secondary)' }}>
            {t('quit.dontWarn')}
          </span>
        </label>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" className="ui-btn ui-btn-secondary" onClick={onCancel}>
            {t('quit.cancel')}
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            style={{ color: 'var(--accent)', borderColor: 'color-mix(in srgb, var(--accent) 40%, var(--border))' }}
            onClick={() => {
              onCancel()
              if (isTauri()) {
                void tauriInvoke('switch_to_web_ui').catch(() => {
                  window.open(getServerUrl() + '/', '_blank', 'noopener,noreferrer')
                })
              } else {
                window.open(getServerUrl() + '/', '_blank', 'noopener,noreferrer')
              }
            }}
          >
            {t('quit.webui')}
          </button>
          <button
            type="button"
            className="ui-btn"
            style={{ background: 'var(--error)', color: '#fff' }}
            onClick={() => onConfirmQuit(dontWarn)}
          >
            {t('quit.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}
