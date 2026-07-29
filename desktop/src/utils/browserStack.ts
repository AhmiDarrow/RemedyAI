/**
 * Native WebView2 embed (child HWND) always paints above React chrome.
 * CSS z-index cannot cover it. Suppress (hide) the embed while overlays /
 * menus cover the host, or when the Browser host is not visible.
 */
import { isTauri, tauriInvoke } from '../api/tauri'

const reasons = new Set<string>()
let lastSent: boolean | null = null

function sync() {
  if (!isTauri()) return
  const suppressed = reasons.size > 0
  if (lastSent === suppressed) return
  lastSent = suppressed
  void tauriInvoke('browser_set_stack_suppressed', { suppressed }).catch(() => {
    /* embed may not exist yet */
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

export function browserStackIsSuppressed(): boolean {
  return reasons.size > 0
}
