/**
 * Desktop host loop for in-house computer use.
 *
 * 1) Polls UI commands so Browser rail opens like Settings (no user pre-open).
 * 2) Claims browser jobs and drives WebView2 (navigate / click / …).
 */
import { useEffect, useRef } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'
import {
  ackComputerUiCommand,
  claimComputerJob,
  completeComputerJob,
  computerCapture,
  computerHostHello,
  emitComputerUi,
  fetchComputerUiCommand,
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

async function navigateInRail(url: string): Promise<string> {
  emitComputerUi({ openBrowser: true })
  // Wait for layout → BrowserSlide mount → bounds
  await new Promise((r) => window.setTimeout(r, 350))
  let { bounds } = await readEmbedBounds()
  if (!bounds) {
    await new Promise((r) => window.setTimeout(r, 250))
    bounds = (await readEmbedBounds()).bounds
  }
  const opened = await tauriInvoke<string>('browser_navigate', {
    url,
    bounds: bounds || null,
  })
  return opened || url
}

async function runBrowserJob(job: ComputerJob): Promise<Record<string, unknown>> {
  const action = (job.action || '').toLowerCase()
  const p = job.payload || {}
  const ui = (p.ui && typeof p.ui === 'object' ? p.ui : {}) as {
    open_browser?: boolean
  }
  if (ui.open_browser || action === 'navigate' || action === 'snapshot') {
    emitComputerUi({ openBrowser: true })
    await new Promise((r) => window.setTimeout(r, 200))
  }

  if (action === 'navigate') {
    const url = String(p.url || '')
    if (!url) throw new Error('url required')
    const opened = await navigateInRail(url)
    return {
      ok: true,
      target: 'browser',
      action: 'navigate',
      message: `Navigated in-rail: ${opened}`,
      url: opened,
    }
  }

  if (action === 'screenshot') {
    emitComputerUi({ openBrowser: true })
    await new Promise((r) => window.setTimeout(r, 200))
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
    const cap = await computerCapture({ label: 'desktop_fallback' })
    return {
      ok: true,
      target: 'desktop',
      action: 'screenshot',
      message: 'No browser bounds yet — full desktop capture',
      ...(cap.capture || {}),
    }
  }

  if (action === 'snapshot' || action === 'a11y') {
    // eval_with_callback returns the elements JSON directly (no page→localhost
    // fetch — that timed out on HTTPS sites like Patreon/Gmail).
    // After optimistic navigate, WebView2 may still be loading — retry once.
    let res = ''
    let lastErr: unknown = null
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        if (attempt > 0) {
          await new Promise((r) => window.setTimeout(r, 400 + attempt * 200))
          try {
            await tauriInvoke<string>('browser_agent_action', {
              action: 'ready',
              job_id: null,
              x: null,
              y: null,
              x2: null,
              y2: null,
              text: null,
              key: null,
              button: null,
              dy: null,
              ref: null,
            })
          } catch {
            /* ready probe best-effort */
          }
        }
        res = await tauriInvoke<string>('browser_agent_action', {
          action: 'snapshot',
          job_id: job.id,
          x: null,
          y: null,
          x2: null,
          y2: null,
          text: null,
          key: null,
          button: null,
          dy: null,
          ref: null,
        })
        lastErr = null
        break
      } catch (e) {
        lastErr = e
      }
    }
    if (lastErr && !res) {
      throw lastErr instanceof Error ? lastErr : new Error(String(lastErr))
    }
    let elements: unknown[] = []
    try {
      const parsed = JSON.parse(res || '[]') as unknown
      if (Array.isArray(parsed)) elements = parsed
      else if (parsed && typeof parsed === 'object' && Array.isArray((parsed as { elements?: unknown }).elements)) {
        elements = (parsed as { elements: unknown[] }).elements
      }
    } catch {
      elements = []
    }
    return {
      ok: true,
      target: 'browser',
      action: 'snapshot',
      message: `${elements.length} interactive elements`,
      elements,
      via: 'eval-callback',
      // Complete immediately — Rust may also complete; both ok.
      _host_defers_complete: false,
    }
  }

  // page_text: dedicated action or legacy click+browser_action payload
  if (
    action === 'page_text' ||
    p.browser_action === 'page_text' ||
    p.page_text
  ) {
    let res = ''
    let lastErr: unknown = null
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        if (attempt > 0) {
          await new Promise((r) => window.setTimeout(r, 350 + attempt * 150))
        }
        res = await tauriInvoke<string>('browser_agent_action', {
          action: 'page_text',
          job_id: null,
          x: null,
          y: null,
          x2: null,
          y2: null,
          text: null,
          key: null,
          button: null,
          dy: null,
          ref: null,
        })
        lastErr = null
        break
      } catch (e) {
        lastErr = e
      }
    }
    if (lastErr && !res) {
      throw lastErr instanceof Error ? lastErr : new Error(String(lastErr))
    }
    let parsed: Record<string, unknown> = {}
    try {
      let v: unknown = JSON.parse(res || '{}')
      // Double-encoded: JSON.stringify inside eval + host string serialization
      if (typeof v === 'string') {
        try {
          v = JSON.parse(v)
        } catch {
          v = { text: v }
        }
      }
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        parsed = v as Record<string, unknown>
      } else {
        parsed = { text: String(v ?? res ?? '') }
      }
    } catch {
      parsed = { text: res }
    }
    return {
      ok: true,
      target: 'browser',
      action: 'page_text',
      message: `Page text ${String(parsed.text || '').length} chars`,
      ...parsed,
    }
  }

  if (['click', 'type', 'key', 'scroll', 'drag', 'click_ref'].includes(action)) {
    const ref = p.ref != null ? String(p.ref) : null
    const text = p.text != null ? String(p.text) : null
    // Atomic click-by-visible-text (preferred for "click Membership options")
    if (action === 'click' && text && !ref && (p.click_text || p.x == null)) {
      const res = await tauriInvoke<string>('browser_agent_action', {
        action: 'click_text',
        job_id: null,
        x: null,
        y: null,
        x2: null,
        y2: null,
        text,
        key: null,
        button: p.button != null ? String(p.button) : null,
        dy: null,
        ref: null,
      })
      const ok = typeof res === 'string' && res.startsWith('ok:')
      return {
        ok,
        target: 'browser',
        action: 'click',
        message: ok
          ? `Clicked text=${text} (${res})`
          : `click_text failed: ${res}`,
        text,
        detail: res,
      }
    }
    const res = await tauriInvoke<string>('browser_agent_action', {
      action: ref && action === 'click' ? 'click' : action,
      x: p.x != null ? Number(p.x) : null,
      y: p.y != null ? Number(p.y) : null,
      x2: p.x2 != null ? Number(p.x2) : null,
      y2: p.y2 != null ? Number(p.y2) : null,
      text,
      key: p.key != null ? String(p.key) : null,
      button: p.button != null ? String(p.button) : null,
      dy: p.dy != null ? Number(p.dy) : null,
      job_id: null,
      ref,
    })
    return {
      ok: true,
      target: 'browser',
      action,
      message: res || `browser:${action}:ok`,
      ref: ref || undefined,
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

/**
 * @param enabled when server is ready
 * @param onOpenBrowser optional — App passes openBrowserInRail for Settings-like panel open
 */
export function useComputerHost(
  enabled = true,
  onOpenBrowser?: () => void,
): void {
  const busy = useRef(false)
  const uiBusy = useRef(false)
  const openBrowserRef = useRef(onOpenBrowser)
  openBrowserRef.current = onOpenBrowser

  useEffect(() => {
    // Desktop shell only. Do not gate on server "ready" — host routes are loopback.
    if (!isTauri()) return
    if (!enabled) return

    let cancelled = false

    const openRail = () => {
      try {
        openBrowserRef.current?.()
      } catch (e) {
        console.warn('[computer-host] openBrowserInRail failed', e)
      }
      emitComputerUi({ openBrowser: true })
    }

    const hello = async () => {
      const { bounds, scale } = await readEmbedBounds()
      await computerHostHello({
        bounds: bounds || undefined,
        scale,
      }).catch(() => null)
    }

    /**
     * SPA only opens the rail — Rust computer-host owns navigate (avoids double
     * navigate + 30s races). Listen to computer-open-browser for URL bar sync.
     */
    const tickUiCommand = async () => {
      if (cancelled || uiBusy.current) return
      uiBusy.current = true
      try {
        // Peek only (do not take) — Rust uses take=1. Just ensure rail is open
        // if anything is pending for UX when SPA is focused.
        const cmd = await fetchComputerUiCommand().catch(() => null)
        if (cmd?.action === 'open_browser' || cmd?.job_action === 'navigate') {
          openRail()
        }
      } finally {
        uiBusy.current = false
      }
    }

    const tickJobs = async () => {
      if (cancelled || busy.current) return
      busy.current = true
      try {
        try {
          await hello()
        } catch (e) {
          console.warn('[computer-host] hello failed', e)
        }
        let job: ComputerJob | null = null
        try {
          // Never claim navigate — Rust computer-host owns rail navigates via
          // ui_command take. Dual claim was racing the WebView main thread and
          // causing second-nav timeouts (Google ok, wiki 8s fail).
          job = await claimComputerJob({ exclude: 'navigate' })
        } catch (e) {
          console.warn('[computer-host] claim failed', e)
          job = null
        }
        if (job?.id && (job.action || '').toLowerCase() === 'navigate') {
          // Belt-and-suspenders: leave for Rust (should not be claimed)
          job = null
        }
        if (job?.id) {
          openRail()
          try {
            const result = await runBrowserJob(job)
            if (result._host_defers_complete) {
              /* a11y push completes */
            } else {
              await completeComputerJob(job.id, {
                ok: result.ok !== false,
                result,
                error:
                  result.ok === false ? String(result.message || 'failed') : undefined,
              })
            }
            await ackComputerUiCommand(job.id).catch(() => null)
          } catch (e) {
            console.warn('[computer-host] job failed', job.id, e)
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

    // Immediate kick — don't wait for first interval
    void (async () => {
      await hello().catch(() => null)
      await tickUiCommand()
      await tickJobs()
    })()
    const helloIv = window.setInterval(() => {
      void hello()
    }, 4000)
    const uiIv = window.setInterval(() => {
      void tickUiCommand()
    }, 250)
    const jobIv = window.setInterval(() => {
      void tickJobs()
    }, 120)

    return () => {
      cancelled = true
      window.clearInterval(helloIv)
      window.clearInterval(uiIv)
      window.clearInterval(jobIv)
    }
  }, [enabled])
}
