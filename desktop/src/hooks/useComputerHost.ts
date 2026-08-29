/**
 * Desktop host loop for in-house computer use.
 *
 * Opens the Browser rail and sends bounds/session hello. Rust computer-host
 * is the only jobs/next poller — this hook must not claim jobs.
 */
import { useEffect, useRef } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'
import {
  computerHostHello,
  emitComputerUi,
  fetchComputerUiCommand,
} from '../api/computer'
import { isConnectCompact } from '../utils/connectMode'

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
    // Phone compact (?connect=1) is not the jobs/next poller — skip hello/peek.
    if (!isTauri()) return
    if (!enabled) return
    if (isConnectCompact()) return

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
    if (isConnectCompact()) return
    void computerHostHello({ sessionId }).catch(() => null)
  }, [enabled, sessionId])
}
