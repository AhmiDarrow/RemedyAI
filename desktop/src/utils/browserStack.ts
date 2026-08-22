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
 * Floating UI that may open over the Browser host: status-bar selects
 * (FormSelect portal, always flips *up* into the workspace), the portaled
 * Theme menu, title-bar menus, dialogs, and in-slide dropdowns. These are
 * small relative to the host, so corner sampling alone misses them.
 */
export const BROWSER_STACK_OVERLAY_SELECTOR = [
  '.settings-portal-select-menu',
  '.remedy-theme-menu',
  '[role="listbox"]',
  '[role="menu"]',
  '[role="dialog"]',
].join(', ')

type RectLike = { left: number; top: number; right: number; bottom: number }

/** True when any overlay rect overlaps the host rect by more than 1px. */
export function overlayCoversHost(host: RectLike, overlays: RectLike[]): boolean {
  for (const o of overlays) {
    if (o.right - o.left < 2 || o.bottom - o.top < 2) continue
    const w = Math.min(o.right, host.right) - Math.max(o.left, host.left)
    const h = Math.min(o.bottom, host.bottom) - Math.max(o.top, host.top)
    if (w > 1 && h > 1) return true
  }
  return false
}

function overlayRectsOutside(host: HTMLElement): RectLike[] {
  const out: RectLike[] = []
  for (const el of document.querySelectorAll<HTMLElement>(BROWSER_STACK_OVERLAY_SELECTOR)) {
    // The host's own loading placeholder / page-fullscreen wrapper are not overlays
    if (host.contains(el) || el.contains(host)) continue
    out.push(el.getBoundingClientRect())
  }
  return out
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
  // Status-bar menus open upward over one corner of the host — the native
  // child would paint over them, so hide it while any overlay intersects.
  if (overlayCoversHost(r, overlayRectsOutside(host))) {
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
