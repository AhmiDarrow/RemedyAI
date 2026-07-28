/**
 * Desktop host loop for in-house computer use.
 * Claims browser-target jobs from the local API and executes them via Tauri
 * (navigate + agent input on the embedded WebView2).
 *
 * Local branch only — do not push until soak tested.
 */
import { useEffect, useRef } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'
import {
  claimComputerJob,
  completeComputerJob,
  computerCapture,
  computerHostHello,
  emitComputerUi,
  type ComputerJob,
} from '../api/computer'

async function readEmbedBounds(): Promise<{
  bounds: { x: number; y: number; width: number; height: number } | null
  scale: number
}> {
  const scale =
    typeof window !== 'undefined' && window.devicePixelRatio
      ? window.devicePixelRatio
      : 1
  try {
    const b = await tauriInvoke<{
      x: number
      y: number
      width: number
      height: number
    } | null>('browser_last_bounds')
    if (b && b.width > 40 && b.height > 40) {
      return { bounds: b, scale }
    }
  } catch {
    /* not open */
  }
  return { bounds: null, scale }
}

async function runBrowserJob(job: ComputerJob): Promise<Record<string, unknown>> {
  const action = (job.action || '').toLowerCase()
  const p = job.payload || {}
  const ui = (p.ui && typeof p.ui === 'object' ? p.ui : {}) as {
    open_browser?: boolean
  }
  if (ui.open_browser || action === 'navigate') {
    emitComputerUi({ openBrowser: true })
    // Give the rail a moment to mount / size before navigate
    await new Promise((r) => window.setTimeout(r, 120))
  }

  if (action === 'navigate') {
    const url = String(p.url || '')
    if (!url) throw new Error('url required')
    const { bounds } = await readEmbedBounds()
    const opened = await tauriInvoke<string>('browser_navigate', {
      url,
      bounds: bounds || null,
    })
    return {
      ok: true,
      target: 'browser',
      action: 'navigate',
      message: `Navigated in-rail: ${opened || url}`,
      url: opened || url,
    }
  }

  if (action === 'screenshot') {
    const { bounds, scale } = await readEmbedBounds()
    if (bounds) {
      const cap = await computerCapture({
        x: Math.round(bounds.x),
        y: Math.round(bounds.y),
        width: Math.round(bounds.width),
        height: Math.round(bounds.height),
        scale,
        label: 'browser_rail',
      })
      const info = cap.capture || {}
      let url = ''
      try {
        url = await tauriInvoke<string>('browser_current_url')
      } catch {
        /* */
      }
      return {
        ok: true,
        target: 'browser',
        action: 'screenshot',
        message: `Browser rail capture (${info.width || '?'}x${info.height || '?'})`,
        ...info,
        bounds,
        scale,
        url,
      }
    }
    // No bounds yet — full desktop via capture API
    const cap = await computerCapture({ label: 'desktop_fallback' })
    return {
      ok: true,
      target: 'desktop',
      action: 'screenshot',
      message: 'No browser bounds yet — full desktop capture',
      ...(cap.capture || {}),
      note: 'open Browser rail for rail-cropped shots',
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
    const hello = async () => {
      const { bounds, scale } = await readEmbedBounds()
      await computerHostHello({
        bounds: bounds || undefined,
        scale,
      }).catch(() => null)
    }

    const tick = async () => {
      if (cancelled || busy.current) return
      busy.current = true
      try {
        await hello()
        const job = await claimComputerJob().catch(() => null)
        if (job?.id) {
          try {
            const result = await runBrowserJob(job)
            await completeComputerJob(job.id, {
              ok: result.ok !== false,
              result,
              error:
                result.ok === false ? String(result.message || 'failed') : undefined,
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
    const helloIv = window.setInterval(() => {
      void hello()
    }, 5000)
    const pollIv = window.setInterval(() => {
      void tick()
    }, 400)

    return () => {
      cancelled = true
      window.clearInterval(helloIv)
      window.clearInterval(pollIv)
    }
  }, [enabled])
}
