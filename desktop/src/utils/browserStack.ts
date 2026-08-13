/**
 * Native WebView2 embed (child HWND) always paints above React chrome.
 * CSS z-index cannot cover it. Suppress (hide) the embed while overlays /
 * menus cover the host, or when the Browser host is not visible.
 *
 * Requires Tauri ACL: `browser_set_stack_suppressed` in allow-browser.
 */
import { isTauri, tauriInvoke } from '../api/tauri'

const reasons = new Set<string>()
let lastSent: boolean | null = null
let lastErrorAt = 0
let inflight: Promise<void> | null = null

function sync() {
  if (!isTauri()) return
  const desired = reasons.size > 0
  if (inflight) return
  if (lastSent === desired) return
  const send = desired
  inflight = tauriInvoke('browser_set_stack_suppressed', { suppressed: send })
    .then(() => {
      lastSent = send
    })
    .catch((err) => {
      lastSent = null
      const now = Date.now()
      if (now - lastErrorAt > 4000) {
        lastErrorAt = now
        console.warn(
          '[remedy] browser_set_stack_suppressed failed — embed may float over chrome:',
          err instanceof Error ? err.message : err,
        )
      }
    })
    .finally(() => {
      inflight = null
      if (lastSent !== (reasons.size > 0)) sync()
    })
}

/** Hold suppress for a named reason; returns release(). */
export function browserStackHold(reason: string): () => void {
  reasons.add(reason)
  sync()
  return () => {
    reasons.delete(reason)
    sync()
  }
}

/** Toggle a named reason (true = hold, false = release). */
export function browserStackSet(reason: string, active: boolean) {
  if (active) reasons.add(reason)
  else reasons.delete(reason)
  sync()
}

/** Host rect is usable and on-screen — clear host-hidden; else suppress. */
export function browserStackSetHostVisible(visible: boolean) {
  browserStackSet('host-hidden', !visible)
}

/**
 * Sample host corners/center via elementFromPoint. If foreign React UI
 * (menus, dialogs) sits over the host, suppress the native HWND.
 */
export function browserStackProbeHostCoverage(host: HTMLElement | null) {
  if (!host || !isTauri() || !host.isConnected) {
    browserStackSet('host-covered', false)
    return
  }
  const r = host.getBoundingClientRect()
  if (r.width < 40 || r.height < 40) {
    browserStackSet('host-covered', true)
    return
  }
  const pts: Array<[number, number]> = [
    [r.left + 12, r.top + 12],
    [r.right - 12, r.top + 12],
    [r.left + 12, r.bottom - 12],
    [r.right - 12, r.bottom - 12],
    [r.left + r.width / 2, r.top + r.height / 2],
  ]
  let foreign = 0
  for (const [x, y] of pts) {
    if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) {
      foreign += 1
      continue
    }
    const el = document.elementFromPoint(x, y)
    if (!el || !host.contains(el)) foreign += 1
  }
  // Majority foreign → something (theme menu, modal, …) covers the host
  browserStackSet('host-covered', foreign >= 3)
}

export function browserStackIsSuppressed(): boolean {
  return reasons.size > 0
}
