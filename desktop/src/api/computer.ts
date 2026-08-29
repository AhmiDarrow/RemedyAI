/** Loopback computer-host API (hello, rail UI). Rust owns jobs/next. */
import { authHeaders, clearApiToken, ensureApiToken, getServerUrl } from './client'

function loopbackApi(): string {
  return `${getServerUrl()}/api`
}

export type BrowserBoundsPayload = {
  x: number
  y: number
  width: number
  height: number
}

function hostHeaders(init?: RequestInit): Record<string, string> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...authHeaders(),
    ...(init?.headers as Record<string, string> | undefined),
  }
  if (init?.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  return headers
}

async function hostFetch<T>(path: string, init?: RequestInit): Promise<T> {
  await ensureApiToken().catch(() => null)
  let res = await fetch(`${loopbackApi()}${path}`, { ...init, headers: hostHeaders(init) })
  if (res.status === 401) {
    clearApiToken()
    await ensureApiToken().catch(() => null)
    res = await fetch(`${loopbackApi()}${path}`, { ...init, headers: hostHeaders(init) })
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`computer host ${path} → ${res.status} ${text.slice(0, 200)}`)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export async function computerHostHello(opts?: {
  bounds?: BrowserBoundsPayload | null
  scale?: number
  sessionId?: string | null
}): Promise<{ host_connected?: boolean }> {
  return hostFetch('/computer/host/hello', {
    method: 'POST',
    body: JSON.stringify({
      client: 'desktop',
      bounds: opts?.bounds || undefined,
      scale: opts?.scale,
      session_id: opts?.sessionId || undefined,
    }),
  })
}

export type ComputerUiCommand = {
  action?: string
  url?: string
  job_id?: string
  job_action?: string
  session_id?: string
  ts?: string
}

/** Peek/take the next rail-open command. */
export async function fetchComputerUiCommand(
  take = false,
  sessionId?: string | null,
): Promise<ComputerUiCommand | null> {
  const params = new URLSearchParams()
  if (take) params.set('take', '1')
  if (sessionId) params.set('session_id', sessionId)
  const q = params.toString() ? `?${params.toString()}` : ''
  const data = await hostFetch<{ command?: ComputerUiCommand | null }>(
    `/computer/ui/command${q}`,
  )
  return data.command || null
}

/** UI event: open the Browser rail. */
export const COMPUTER_UI_EVENT = 'remedy:computer-ui'

export function emitComputerUi(detail: {
  openBrowser?: boolean
  keepSettings?: boolean
}): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(COMPUTER_UI_EVENT, { detail }))
}

/** Destroy the native Browser embed (wizard / OAuth have no rail ✕). */
export async function closeBrowserRail(): Promise<void> {
  try {
    const { isTauri, tauriInvoke } = await import('./tauri')
    if (!isTauri()) return
    await tauriInvoke('browser_close')
  } catch {
    /* already gone */
  }
}

/** Open a URL in the in-rail Browser. `openRail: false` for wizard hosts. */
export async function openUrlInBrowserRail(
  url: string,
  opts?: { keepSettings?: boolean; openRail?: boolean },
): Promise<'rail' | 'external'> {
  const trimmed = (url || '').trim()
  if (!trimmed) return 'external'
  try {
    const { isTauri, tauriInvoke } = await import('./tauri')
    if (isTauri()) {
      if (opts?.openRail !== false) {
        emitComputerUi({
          openBrowser: true,
          keepSettings: Boolean(opts?.keepSettings),
        })
        await new Promise((r) => window.setTimeout(r, 350))
      } else {
        // Wizard host paints in the same tick as open; wait one layout.
        await new Promise((r) => window.setTimeout(r, 160))
      }
      // Best-effort bounds from Browser slide host
      let bounds: BrowserBoundsPayload | null = null
      try {
        const el = document.querySelector('[data-browser-embed-host]') as HTMLElement | null
        if (el) {
          const r = el.getBoundingClientRect()
          if (r.width > 40 && r.height > 40) {
            bounds = {
              x: Math.round(r.x),
              y: Math.round(r.y),
              width: Math.round(r.width),
              height: Math.round(r.height),
            }
          }
        }
      } catch {
        /* layout later */
      }
      await tauriInvoke('browser_navigate', { url: trimmed, bounds })
      return 'rail'
    }
  } catch {
    /* fall through */
  }
  try {
    const { openExternalUrl } = await import('./auth')
    await openExternalUrl(trimmed)
  } catch {
    if (typeof window !== 'undefined') window.open(trimmed, '_blank', 'noopener,noreferrer')
  }
  return 'external'
}
