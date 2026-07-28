/** Computer-use host bridge (desktop claims browser jobs from the local API). */
import { apiFetch } from './client'

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

export async function computerHostHello(opts?: {
  bounds?: BrowserBoundsPayload | null
  scale?: number
}): Promise<{ host_connected?: boolean }> {
  return apiFetch('/computer/host/hello', {
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
}> {
  return apiFetch('/computer/host/status')
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

export async function claimComputerJob(): Promise<ComputerJob | null> {
  const data = await apiFetch<{ job?: ComputerJob | null }>('/computer/jobs/next')
  return data.job || null
}

export async function completeComputerJob(
  jobId: string,
  body: { ok: boolean; result?: Record<string, unknown>; error?: string },
): Promise<void> {
  await apiFetch(`/computer/jobs/${encodeURIComponent(jobId)}/complete`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** UI event: open the Browser rail for agent computer use. */
export const COMPUTER_UI_EVENT = 'remedy:computer-ui'

export function emitComputerUi(detail: { openBrowser?: boolean }): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(COMPUTER_UI_EVENT, { detail }))
}
