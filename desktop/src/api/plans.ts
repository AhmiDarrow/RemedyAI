/** Session-scoped task plans (Plan mode). */
import { apiFetch } from './client'

export type PlanStep = {
  id: string
  title: string
  detail?: string
  status?: string
}

export type TaskPlan = {
  id: string
  title: string
  goal?: string
  status?: string
  steps?: PlanStep[]
  risks?: string[]
  session_id?: string | null
}

/**
 * Latest plan for *sessionId* only. Never returns another session's plan.
 * Pass null session → global latest (rare; banner always passes session).
 */
export async function fetchLatestPlan(
  sessionId: string | null,
): Promise<TaskPlan | null> {
  if (!sessionId) return null
  try {
    const data = await apiFetch<{ plan?: TaskPlan | null } | TaskPlan>(
      `/plans/latest?session_id=${encodeURIComponent(sessionId)}`,
    )
    const p =
      data && typeof data === 'object' && 'plan' in data
        ? (data as { plan?: TaskPlan | null }).plan
        : (data as TaskPlan)
    if (!p || !p.id) return null
    // Defense in depth: ignore mismatched session tags
    if (p.session_id && p.session_id !== sessionId) return null
    return p
  } catch {
    return null
  }
}
