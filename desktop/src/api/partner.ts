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

export interface PartnerStatus {
  pending_approvals: number
  open_goals: number
  access_scope: string
  harness_mode: string
  brief_intent: string
  approvals: PendingApproval[]
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
