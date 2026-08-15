/** Computer-use host bridge (desktop claims browser jobs from the local API). */
import { apiFetch, authHeaders, clearApiToken, ensureApiToken, getServerUrl } from './client'

function loopbackApi(): string {
  return `${getServerUrl()}/api`
}

export type ComputerJob = {
  id: string
  action: string
  payload: Record<string, unknown>
  status: string
  result?: Record<string, unknown> | null
  error?: string | null
}

export type BrowserBoundsPayload = {
  x: number
  y: number
  width: number
  height: number
}

/**
 * Loopback host calls — Bearer required (S-AUTH-04). We still try ensureApiToken
 * first so late SPA bootstrap does not 401; Rust poller loads the DPAPI token
 * independently when the React host is not mounted.
 */
async function hostFetch<T>(path: string, init?: RequestInit): Promise<T> {
  await ensureApiToken().catch(() => null)
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...authHeaders(),
    ...(init?.headers as Record<string, string> | undefined),
  }
  if (init?.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }
  let res = await fetch(`${loopbackApi()}${path}`, {
    ...init,
    headers,
  })
  if (res.status === 401) {
    clearApiToken()
    await ensureApiToken().catch(() => null)
    const retryHeaders: Record<string, string> = {
      Accept: 'application/json',
      ...authHeaders(),
      ...(init?.headers as Record<string, string> | undefined),
    }
    if (init?.body && !retryHeaders['Content-Type']) {
      retryHeaders['Content-Type'] = 'application/json'
    }
    res = await fetch(`${loopbackApi()}${path}`, {
      ...init,
      headers: retryHeaders,
    })
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

export async function computerHostStatus(): Promise<{
  host_connected?: boolean
  browser_bounds?: (BrowserBoundsPayload & { scale?: number }) | null
  pending_jobs?: number
}> {
  try {
    return await hostFetch('/computer/host/status')
  } catch {
    return apiFetch('/computer/host/status')
  }
}

export async function computerCapture(body: {
  x?: number
  y?: number
  width?: number
  height?: number
  scale?: number
  label?: string
}): Promise<{ ok?: boolean; capture?: Record<string, unknown> }> {
  return apiFetch('/computer/capture', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** Claim next job. SPA must exclude navigate — Rust owns rail navigates. */
export async function claimComputerJob(
  opts?: { exclude?: string; sessionId?: string | null },
): Promise<ComputerJob | null> {
  const exclude = opts?.exclude ?? 'navigate'
  const params = new URLSearchParams()
  if (exclude) params.set('exclude', exclude)
  if (opts?.sessionId) params.set('session_id', opts.sessionId)
  const q = params.toString() ? `?${params.toString()}` : ''
  const data = await hostFetch<{ job?: ComputerJob | null }>(
    `/computer/jobs/next${q}`,
  )
  return data.job || null
}

export async function completeComputerJob(
  jobId: string,
  body: { ok: boolean; result?: Record<string, unknown>; error?: string },
): Promise<void> {
  await hostFetch(`/computer/jobs/${encodeURIComponent(jobId)}/complete`, {
    method: 'POST',
    body: JSON.stringify(body),
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

/** Server asks Desktop to open Browser rail (like Settings) + optional URL. */
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

export async function ackComputerUiCommand(jobId?: string | null): Promise<void> {
  const q = jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''
  await hostFetch(`/computer/ui/command/ack${q}`, { method: 'POST' })
}

/** UI event: open the Browser rail for agent computer use. */
export const COMPUTER_UI_EVENT = 'remedy:computer-ui'

export function emitComputerUi(detail: {
  openBrowser?: boolean
  keepSettings?: boolean
}): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(COMPUTER_UI_EVENT, { detail }))
}

/**
 * Open a URL in Remedy's in-rail Browser (default for webpages).
 * Falls back to system browser only if Tauri navigate is unavailable.
 */
export async function openUrlInBrowserRail(
  url: string,
  opts?: { keepSettings?: boolean },
): Promise<'rail' | 'external'> {
  const trimmed = (url || '').trim()
  if (!trimmed) return 'external'
  try {
    const { isTauri, tauriInvoke } = await import('./tauri')
    if (isTauri()) {
      emitComputerUi({
        openBrowser: true,
        keepSettings: Boolean(opts?.keepSettings),
      })
      await new Promise((r) => window.setTimeout(r, 350))
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
