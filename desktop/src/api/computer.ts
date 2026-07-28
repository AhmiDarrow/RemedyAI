/** Computer-use host bridge (desktop claims browser jobs from the local API). */
import { apiFetch, authHeaders, ensureApiToken } from './client'

const LOOPBACK_API = 'http://127.0.0.1:7400/api'

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
 * Loopback host calls — server allows these without Bearer from 127.0.0.1
 * so the poller works even when token bootstrap is late.
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
  const res = await fetch(`${LOOPBACK_API}${path}`, {
    ...init,
    headers,
  })
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
}): Promise<{ host_connected?: boolean }> {
  return hostFetch('/computer/host/hello', {
    method: 'POST',
    body: JSON.stringify({
      client: 'desktop',
      bounds: opts?.bounds || undefined,
      scale: opts?.scale,
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
  opts?: { exclude?: string },
): Promise<ComputerJob | null> {
  const exclude = opts?.exclude ?? 'navigate'
  const q = exclude ? `?exclude=${encodeURIComponent(exclude)}` : ''
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
  ts?: string
}

/** Server asks Desktop to open Browser rail (like Settings) + optional URL. */
export async function fetchComputerUiCommand(
  take = false,
): Promise<ComputerUiCommand | null> {
  const q = take ? '?take=1' : ''
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

export function emitComputerUi(detail: { openBrowser?: boolean }): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(COMPUTER_UI_EVENT, { detail }))
}
