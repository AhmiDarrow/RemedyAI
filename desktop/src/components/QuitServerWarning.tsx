import { useEffect, useState } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'

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
  const [dontWarn, setDontWarn] = useState(false)

  useEffect(() => {
    if (open) setDontWarn(false)
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[220] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="quit-warn-title"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-xl p-5 shadow-2xl"
        style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border)',
          color: 'var(--text-primary)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          id="quit-warn-title"
          className="text-sm font-semibold mb-2"
          style={{ color: 'var(--error, #f87171)' }}
        >
          Quit Remedy and stop the local server?
        </div>
        <div className="text-xs leading-relaxed mb-3" style={{ color: 'var(--text-secondary)' }}>
          <p className="mb-2">
            Quitting fully <strong>stops the local API</strong> on{' '}
            <code style={{ color: 'var(--accent)' }}>127.0.0.1:7400</code>.
            Any browser WebUI will disconnect and stop working.
          </p>
          <p className="mb-2">
            To keep chatting in the browser, use <strong>Switch to WebUI</strong> instead —
            that hides the desktop window to the tray and leaves the server running.
          </p>
          <p style={{ color: 'var(--text-muted)' }}>
            Closing the window (✕) always hides to the tray and keeps the server up.
            Use tray <strong>Quit</strong> when you want a full stop.
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
            Don&apos;t show this warning again
          </span>
        </label>
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg text-xs"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border)',
            }}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--accent)',
              border: '1px solid var(--accent)',
            }}
            onClick={() => {
              onCancel()
              if (isTauri()) {
                void tauriInvoke('switch_to_web_ui').catch(() => {
                  window.open('http://127.0.0.1:7400/', '_blank', 'noopener,noreferrer')
                })
              } else {
                window.open('http://127.0.0.1:7400/', '_blank', 'noopener,noreferrer')
              }
            }}
          >
            Switch to WebUI
          </button>
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: 'var(--error, #ef4444)', color: '#fff' }}
            onClick={() => onConfirmQuit(dontWarn)}
          >
            Quit and stop server
          </button>
        </div>
      </div>
    </div>
  )
}
