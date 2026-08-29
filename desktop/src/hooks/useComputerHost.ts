/**
 * Desktop host loop for in-house computer use.
 *
 * 1) Polls UI commands so Browser rail opens like Settings (no user pre-open).
 * 2) Hello (~4s) is bounds + session only.
 * 3) Rust computer-host is the only jobs/next poller. This hook used to claim
 *    click/type/snapshot too, which dual-spammed the API and raced the native
 *    driver. Keep runBrowserJob for tests / a future fallback.
 */
import { useEffect, useRef } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'
import {
  computerCapture,
  computerHostHello,
  emitComputerUi,
  fetchComputerUiCommand,
  type ComputerJob,
} from '../api/computer'

/** Rust in-band failures are strings like missing-ref: / no-match: / no element. */
export function rustBrowserActionOk(res: unknown): boolean {
  if (typeof res !== 'string') return false
  const s = res.trim()
  if (!s) return false
  if (
    s.startsWith('missing-ref:')
    || s.startsWith('no-match:')
    || s.startsWith('no-option:')
    || s.startsWith('not-select:')
    || s.startsWith('no element')
    || s.startsWith('ambiguous:')
    || s.startsWith('error:')
  ) {
    return false
  }
  // Rust click uses "ok:…"; type/key/scroll return "ok" or "ok-fallback".
  // An empty eval result is minted as "browser:{act}:no-result" by the host
  // (the script threw) and must read as failure.
  return s === 'ok' || s.startsWith('ok:') || s.startsWith('ok-')
}

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

export async function runBrowserJob(job: ComputerJob): Promise<Record<string, unknown>> {
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
    // 2 attempts × 9s rail evals fits the executor's 22s snapshot wait.
    for (let attempt = 0; attempt < 2; attempt++) {
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
    // 2 attempts × 9s rail evals fits the executor's 20s page_text wait.
    for (let attempt = 0; attempt < 2; attempt++) {
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

  // Press-and-hold: trusted hold gesture (verification walls, hold-to-confirm).
  // hold_ms rides the `dy` slot. Rust resolves x/y or locates by text/ref.
  if (action === 'press_hold') {
    const res = await tauriInvoke<string>('browser_agent_action', {
      action: 'press_hold',
      x: p.x != null ? Number(p.x) : null,
      y: p.y != null ? Number(p.y) : null,
      x2: null,
      y2: null,
      text: p.text != null ? String(p.text) : null,
      key: null,
      button: null,
      dy: p.hold_ms != null ? Number(p.hold_ms) : p.dy != null ? Number(p.dy) : null,
      job_id: null,
      ref: p.ref != null ? String(p.ref) : null,
    })
    const ok = rustBrowserActionOk(res)
    return {
      ok,
      target: 'browser',
      action: 'press_hold',
      message: ok ? `Pressed and held (${res})` : `press_hold failed: ${res}`,
      detail: res,
    }
  }

  if (['click', 'type', 'key', 'scroll', 'drag', 'click_ref', 'select'].includes(action)) {
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
      const ok = rustBrowserActionOk(res)
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
      key:
        p.key != null
          ? String(p.key)
          : action === 'type' || action === 'select'
            ? (p.query != null
                ? String(p.query)
                : p.label != null
                  ? String(p.label)
                  : p.hint != null
                    ? String(p.hint)
                    : null)
            : null,
      button: p.button != null ? String(p.button) : null,
      dy: p.dy != null ? Number(p.dy) : null,
      job_id: null,
      ref,
    })
    const ok = rustBrowserActionOk(res)
    return {
      ok,
      target: 'browser',
      action,
      message: ok ? (res || `browser:${action}:ok`) : `browser:${action} failed: ${res}`,
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
  sessionId?: string | null,
): void {
  const uiBusy = useRef(false)
  const openBrowserRef = useRef(onOpenBrowser)
  openBrowserRef.current = onOpenBrowser
  const sessionIdRef = useRef(sessionId)
  sessionIdRef.current = sessionId

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
        sessionId: sessionIdRef.current,
      }).catch(() => null)
    }

    /**
     * SPA only opens the rail — Rust computer-host owns every browser job.
     * Peek (do not take) so the native poller still consumes ui/command.
     */
    const tickUiCommand = async (): Promise<boolean> => {
      if (cancelled || uiBusy.current) return false
      uiBusy.current = true
      try {
        const cmd = await fetchComputerUiCommand(
          false,
          sessionIdRef.current,
        ).catch(() => null)
        if (cmd?.action === 'open_browser' || cmd?.job_action === 'navigate') {
          openRail()
          return true
        }
        return false
      } finally {
        uiBusy.current = false
      }
    }

    const HELLO_MS = 4000
    const UI_BUSY_MS = 250
    const UI_IDLE_MS = 800
    const UI_IDLE_MAX_MS = 2000
    let helloIv = 0
    let uiIv = 0
    let loopsOn = false
    let loopGen = 0
    let uiIdleStreak = 0

    const stopLoops = () => {
      loopGen += 1
      window.clearTimeout(helloIv)
      window.clearTimeout(uiIv)
      helloIv = 0
      uiIv = 0
      loopsOn = false
    }

    const isHidden = () =>
      typeof document !== 'undefined' &&
      (document.hidden || document.visibilityState === 'hidden')

    const scheduleHello = () => {
      const my = loopGen
      window.clearTimeout(helloIv)
      helloIv = window.setTimeout(() => {
        void hello().finally(() => {
          if (!cancelled && loopsOn && my === loopGen) scheduleHello()
        })
      }, HELLO_MS)
    }

    const scheduleUi = (ms: number) => {
      const my = loopGen
      window.clearTimeout(uiIv)
      uiIv = window.setTimeout(() => {
        void tickUiCommand().then((hadCmd) => {
          if (cancelled || !loopsOn || my !== loopGen) return
          if (hadCmd) {
            uiIdleStreak = 0
            scheduleUi(UI_BUSY_MS)
            return
          }
          uiIdleStreak += 1
          const idleMs = uiIdleStreak >= 8 ? UI_IDLE_MAX_MS : UI_IDLE_MS
          scheduleUi(idleMs)
        })
      }, ms)
    }

    const reschedule = () => {
      stopLoops()
      if (isHidden()) return
      loopsOn = true
      uiIdleStreak = 0
      scheduleHello()
      scheduleUi(UI_BUSY_MS)
    }

    void (async () => {
      await hello().catch(() => null)
      await tickUiCommand()
      if (!cancelled) reschedule()
    })()
    const onVis = () => reschedule()
    document.addEventListener('visibilitychange', onVis)

    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVis)
      stopLoops()
    }
  }, [enabled])

  // Stamp focused session as soon as the open tab changes (do not wait 4s hello).
  useEffect(() => {
    if (!isTauri() || !enabled || !sessionId) return
    void computerHostHello({ sessionId }).catch(() => null)
  }, [enabled, sessionId])
}
