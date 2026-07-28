/**
 * Desktop host loop for in-house computer use.
 * Claims browser-target jobs from the local API and executes them via Tauri
 * (navigate + agent input on the embedded WebView2).
 *
 * Local-only feature branch work — do not push until soak tested.
 */
import { useEffect, useRef } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'
import {
  claimComputerJob,
  completeComputerJob,
  computerHostHello,
  type ComputerJob,
} from '../api/computer'

async function runBrowserJob(job: ComputerJob): Promise<Record<string, unknown>> {
  const action = (job.action || '').toLowerCase()
  const p = job.payload || {}

  if (action === 'navigate') {
    const url = String(p.url || '')
    if (!url) throw new Error('url required')
    const opened = await tauriInvoke<string>('browser_navigate', { url, bounds: null })
    return {
      ok: true,
      target: 'browser',
      action: 'navigate',
      message: `Navigated in-rail: ${opened || url}`,
      url: opened || url,
    }
  }

  if (action === 'screenshot') {
    // Full desktop capture is done server-side when host is offline.
    // With host: report bounds so the model knows the rail geometry; full
    // embed PNG capture lands in a later slice.
    let bounds: unknown = null
    try {
      bounds = await tauriInvoke('browser_last_bounds')
    } catch {
      /* ignore */
    }
    let url = ''
    try {
      url = await tauriInvoke<string>('browser_current_url')
    } catch {
      /* ignore */
    }
    return {
      ok: true,
      target: 'browser',
      action: 'screenshot',
      message:
        'Browser host live — use target=desktop for full-screen PNG path until embed capture ships; bounds returned.',
      bounds,
      url,
      note: 'embed_png_pending',
    }
  }

  if (['click', 'type', 'key', 'scroll', 'drag'].includes(action)) {
    const res = await tauriInvoke<string>('browser_agent_action', {
      action,
      x: p.x != null ? Number(p.x) : null,
      y: p.y != null ? Number(p.y) : null,
      x2: p.x2 != null ? Number(p.x2) : null,
      y2: p.y2 != null ? Number(p.y2) : null,
      text: p.text != null ? String(p.text) : null,
      key: p.key != null ? String(p.key) : null,
      button: p.button != null ? String(p.button) : null,
      dy: p.dy != null ? Number(p.dy) : null,
    })
    return {
      ok: true,
      target: 'browser',
      action,
      message: res || `browser:${action}:ok`,
    }
  }

  if (action === 'windows') {
    return {
      ok: false,
      target: 'browser',
      action: 'windows',
      message: 'computer_windows is desktop-only; set target=desktop',
    }
  }

  throw new Error(`unsupported browser job action: ${action}`)
}

export function useComputerHost(enabled = true): void {
  const busy = useRef(false)

  useEffect(() => {
    if (!enabled || !isTauri()) return

    let cancelled = false
    const tick = async () => {
      if (cancelled || busy.current) return
      busy.current = true
      try {
        await computerHostHello().catch(() => null)
        const job = await claimComputerJob().catch(() => null)
        if (job?.id) {
          try {
            const result = await runBrowserJob(job)
            await completeComputerJob(job.id, {
              ok: result.ok !== false,
              result,
              error: result.ok === false ? String(result.message || 'failed') : undefined,
            })
          } catch (e) {
            await completeComputerJob(job.id, {
              ok: false,
              error: e instanceof Error ? e.message : String(e),
            }).catch(() => null)
          }
        }
      } finally {
        busy.current = false
      }
    }

    void tick()
    const hello = window.setInterval(() => {
      void computerHostHello().catch(() => null)
    }, 5000)
    const poll = window.setInterval(() => {
      void tick()
    }, 400)

    return () => {
      cancelled = true
      window.clearInterval(hello)
      window.clearInterval(poll)
    }
  }, [enabled])
}
