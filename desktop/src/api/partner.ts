import { apiFetch } from './client'

export interface PendingApproval {
  id: string
  tool_name: string
  command: string
  reason: string
  session_id?: string | null
  status: string
  created_at?: number
}

export interface ProviderHealthHint {
  flaky?: boolean
  suggest_switch?: boolean
  suggested_provider?: string | null
  reason?: string | null
  health?: {
    error_rate?: number
    rate_limit_hits?: number
    avg_latency_ms?: number | null
    samples?: number
  }
}

export interface PartnerStatus {
  pending_approvals: number
  open_goals: number
  access_scope: string
  harness_mode: string
  brief_intent: string
  approvals: PendingApproval[]
  provider_health?: ProviderHealthHint
}

export async function getPartnerStatus(): Promise<PartnerStatus> {
  return apiFetch<PartnerStatus>('/partner/status')
}

export async function listApprovals(sessionId?: string | null): Promise<PendingApproval[]> {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  const data = await apiFetch<{ approvals: PendingApproval[] }>(`/approvals${q}`)
  return data.approvals || []
}

export async function resolveApproval(
  id: string,
  approve: boolean,
  scope: 'session' | 'always' = 'session',
): Promise<{ status: string; hint?: string }> {
  return apiFetch(`/approvals/${encodeURIComponent(id)}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ approve, scope }),
  })
}

// --- Plans & checkpoints (personal partner Phase B) ---

export type Checkpoint = {
  id: string
  session_id?: string | null
  title: string
  done?: string[]
  next_steps?: string[]
  tools_used?: string[]
  tool_step_count?: number
  failures?: string[]
  reason?: string
  created_at?: string
}

export type TaskPlan = {
  id: string
  title: string
  goal?: string
  status?: string
  steps?: { id: string; title: string; status?: string }[]
  risks?: string[]
  session_id?: string | null
}

export async function getLatestCheckpoint(
  sessionId?: string | null,
): Promise<{ checkpoint: Checkpoint | null; markdown?: string }> {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`/checkpoints/latest${q}`)
}

export async function listCheckpoints(
  sessionId?: string | null,
  limit = 10,
): Promise<Checkpoint[]> {
  const params = new URLSearchParams()
  if (sessionId) params.set('session_id', sessionId)
  params.set('limit', String(limit))
  const data = await apiFetch<{ checkpoints: Checkpoint[] }>(
    `/checkpoints?${params.toString()}`,
  )
  return data.checkpoints || []
}

export async function getLatestPlan(
  sessionId?: string | null,
): Promise<{ plan: TaskPlan | null; markdown?: string }> {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch(`/plans/latest${q}`)
}

export async function approvePlan(planId: string): Promise<TaskPlan | null> {
  const data = await apiFetch<{ plan: TaskPlan }>(
    `/plans/${encodeURIComponent(planId)}/status`,
    {
      method: 'POST',
      body: JSON.stringify({ status: 'approved' }),
    },
  )
  return data.plan || null
}

export async function getSkillReuseMetrics(): Promise<{
  total_activations: number
  skills_with_activation: number
  multi_session_reactivations: number
  skills: { name: string; activations: number }[]
}> {
  return apiFetch('/skills/metrics/reuse')
}
