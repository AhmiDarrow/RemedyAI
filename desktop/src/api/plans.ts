/** Session-scoped task plans (Plan mode). */
import { apiFetch } from './client'

export type PlanStep = {
  id: string
  title: string
  detail?: string
  status?: string
  intended?: string
  observed?: string
  evidence?: string
  block_reason?: string
}

export type PlanStatus = 'draft' | 'approved' | 'active' | 'done' | 'cancelled' | string

export type TaskPlan = {
  id: string
  title: string
  goal?: string
  status?: PlanStatus
  steps?: PlanStep[]
  risks?: string[]
  session_id?: string | null
}

/** Terminal statuses: no Approve chrome; banner should not stick. */
export const PLAN_TERMINAL_STATUSES = new Set(['done', 'cancelled'])

/** Statuses that still need user attention or are mid-build. */
export const PLAN_ACTIONABLE_STATUSES = new Set(['draft', 'approved', 'active'])

export function isPlanTerminal(status?: string | null): boolean {
  return PLAN_TERMINAL_STATUSES.has(String(status || '').toLowerCase())
}

export function isPlanActionable(status?: string | null): boolean {
  const s = String(status || 'draft').toLowerCase()
  return PLAN_ACTIONABLE_STATUSES.has(s)
}

/**
 * Whether the sticky Plan banner should render for this session state.
 *
 * - **Plan mode**: always (empty state, draft, or review mid-flight).
 * - **Build mode + draft**: show so the user can still Approve → Build.
 * - **Build mode + approved/active**: hide — plan is already in motion;
 *   the full card only steals chat space. Re-open Plan (Ctrl+B) to revise/cancel.
 * - **done / cancelled**: never stick in Build mode.
 */
export function shouldShowPlanBanner(
  plan: TaskPlan | null,
  planMode: boolean,
): boolean {
  if (planMode) return true
  if (!plan?.id) return false
  const s = String(plan.status || 'draft').toLowerCase()
  // Work already kicked off — don't pin the plan card over the feed.
  if (s === 'approved' || s === 'active') return false
  if (s === 'done' || s === 'cancelled') return false
  // Draft still waiting for Approve even if the user flipped to Build early.
  return s === 'draft'
}

/**
 * Latest plan for *sessionId* only. Never returns another session's plan.
 * When *actionableOnly*, skips done/cancelled so the banner does not stick
 * after the work is finished or the user quits the plan.
 */
export async function fetchLatestPlan(
  sessionId: string | null,
  opts?: { actionableOnly?: boolean },
): Promise<TaskPlan | null> {
  if (!sessionId) return null
  try {
    const params = new URLSearchParams({ session_id: sessionId })
    if (opts?.actionableOnly) params.set('actionable', '1')
    const data = await apiFetch<{ plan?: TaskPlan | null } | TaskPlan>(
      `/plans/latest?${params.toString()}`,
    )
    const p =
      data && typeof data === 'object' && 'plan' in data
        ? (data as { plan?: TaskPlan | null }).plan
        : (data as TaskPlan)
    if (!p || !p.id) return null
    // Defense in depth: ignore mismatched session tags
    if (p.session_id && p.session_id !== sessionId) return null
    if (opts?.actionableOnly && isPlanTerminal(p.status)) return null
    return p
  } catch {
    return null
  }
}

export async function setPlanStatus(
  planId: string,
  status: PlanStatus,
): Promise<TaskPlan | null> {
  const data = await apiFetch<{ plan?: TaskPlan }>(
    `/plans/${encodeURIComponent(planId)}/status`,
    {
      method: 'POST',
      body: JSON.stringify({ status }),
    },
  )
  return data.plan || null
}

export async function approvePlan(planId: string): Promise<TaskPlan | null> {
  return setPlanStatus(planId, 'approved')
}

export async function cancelPlan(planId: string): Promise<TaskPlan | null> {
  return setPlanStatus(planId, 'cancelled')
}
