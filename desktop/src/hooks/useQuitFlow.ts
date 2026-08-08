/**
 * Quit / tray-warning flow extracted from App.tsx so shell chrome stays thin.
 */
import { useCallback } from 'react'
import { getServerUrl } from '../api/client'

function isTauri(): boolean {
  if (typeof window === 'undefined') return false
  const w = window as any
  return !!(w.__TAURI__ || w.__TAURI_INTERNALS__ || w.isTauri)
}

export function useQuitFlow(opts: {
  setQuitWarnOpen: (open: boolean) => void
}): {
  confirmQuitApp: (dontWarnAgain: boolean) => Promise<void>
  requestQuitWithWarning: () => Promise<void>
  isTauri: () => boolean
} {
  const { setQuitWarnOpen } = opts

  const confirmQuitApp = useCallback(
    async (dontWarnAgain: boolean) => {
      setQuitWarnOpen(false)
      if (!isTauri()) {
        window.close()
        return
      }
      const { tauriInvoke } = await import('../api/tauri')
      // Must finish writing desktop.json BEFORE quit_app kills the process —
      // fire-and-forget prefs save was why "Don't show again" never stuck.
      if (dontWarnAgain) {
        try {
          localStorage.setItem('remedy.skipQuitServerWarning', '1')
        } catch {
          /* */
        }
        try {
          const prefs = await tauriInvoke<{
            close_to_tray?: boolean
            start_in_tray?: boolean
          }>('get_desktop_prefs')
          await tauriInvoke('set_desktop_prefs', {
            close_to_tray: Boolean(prefs?.close_to_tray ?? true),
            start_in_tray: Boolean(prefs?.start_in_tray ?? false),
            skip_quit_server_warning: true,
          })
        } catch (e) {
          console.warn('save skip_quit_server_warning:', e)
        }
      }
      try {
        await Promise.race([
          tauriInvoke('quit_app'),
          new Promise<void>((resolve) => window.setTimeout(resolve, 2500)),
        ])
      } catch (e) {
        console.warn('quit_app failed:', e)
      }
    },
    [setQuitWarnOpen],
  )

  const requestQuitWithWarning = useCallback(async () => {
    if (!isTauri()) {
      setQuitWarnOpen(true)
      return
    }
    try {
      const { tauriInvoke } = await import('../api/tauri')
      try {
        if (localStorage.getItem('remedy.skipQuitServerWarning') === '1') {
          await tauriInvoke('quit_app')
          return
        }
      } catch {
        /* */
      }
      try {
        const prefs = await tauriInvoke<{ skip_quit_server_warning?: boolean }>(
          'get_desktop_prefs',
        )
        if (prefs?.skip_quit_server_warning) {
          try {
            localStorage.setItem('remedy.skipQuitServerWarning', '1')
          } catch {
            /* */
          }
          await tauriInvoke('quit_app')
          return
        }
      } catch {
        /* fall through to confirm path */
      }
      const res = await tauriInvoke<{ needs_confirm?: boolean; quitting?: boolean }>(
        'request_quit_app',
      )
      if (res?.needs_confirm) {
        setQuitWarnOpen(true)
      }
    } catch {
      setQuitWarnOpen(true)
    }
  }, [setQuitWarnOpen])

  return { confirmQuitApp, requestQuitWithWarning, isTauri }
}

/** Used by menu actions that need the server base URL. */
export function openWebUiFallback(): string {
  return getServerUrl() + '/'
}
