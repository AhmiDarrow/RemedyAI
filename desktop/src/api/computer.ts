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

export async function computerHostHello(): Promise<{ host_connected?: boolean }> {
  return apiFetch('/computer/host/hello', {
    method: 'POST',
    body: JSON.stringify({ client: 'desktop' }),
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
