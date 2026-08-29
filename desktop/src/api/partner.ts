import { apiFetch } from './client'

export interface PendingApproval {
  id: string
  tool_name: string
  command: string
  reason: string
  /** Plain-language headline the owner can act on ("Remedy wants to…"). */
  summary?: string
  /** Payment / credential / vault owner checkpoint — asks in every mode. */
  sensitive?: boolean
  soft_risk?: string | null
  approval_mode_hint?: string
  session_id?: string | null
  status: string
  created_at?: number
  /** Yes / No / Explain for life-task plan cards. */
  choices?: string[]
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

/** Organism mood from Soul Field (status bar + tray tooltip). */
export interface SomaStatus {
  mood?: string
  emoji?: string
  label?: string
  rapport?: number
  trust?: number
  last_stance?: string
  open_threads?: number
  episodes?: number
  muscle_hint?: string
  tray_tooltip?: string
  ts?: number
}

export type LifeGoal = {
  id: string
  title: string
  why?: string
  horizon?: string
  done_looks_like?: string
  next_action?: string
  next_by?: string
  status?: string
  evidence?: string[]
}

export type LifeLastStep = {
  ts?: number
  goal?: string
  did?: string
  next?: string
  path?: string
  kind?: string
}

export type LifeBoard = {
  goals: LifeGoal[]
  life_folder?: string | null
  last_step?: LifeLastStep | null
  digest?: string | null
}

export async function getLifeBoard(): Promise<LifeBoard> {
  const data = await apiFetch<LifeBoard & { goals?: LifeGoal[] }>('/goals')
  return {
    goals: data.goals || [],
    life_folder: data.life_folder || null,
    last_step: data.last_step || null,
    digest: data.digest || null,
  }
}

export async function listLifeGoals(): Promise<LifeGoal[]> {
  const board = await getLifeBoard()
  return board.goals
}

export async function createLifeGoal(title: string): Promise<LifeGoal> {
  return apiFetch<LifeGoal>('/goals', {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

export async function patchLifeGoal(
  id: string,
  fields: Partial<Pick<LifeGoal, 'status' | 'next_action' | 'next_by' | 'done_looks_like' | 'why'>> & {
    evidence?: string
  },
): Promise<LifeGoal> {
  return apiFetch<LifeGoal>(`/goals/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
  })
}

export async function deleteLifeGoal(id: string): Promise<void> {
  await apiFetch(`/goals/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** Clear the "Last:" activity pill history (drive steps). */
export async function clearLifeActivity(): Promise<void> {
  await apiFetch('/goals/activity/clear', { method: 'POST' })
}

/** Rename a goal's title (edit until it becomes a real, worked goal). */
export async function renameLifeGoal(id: string, title: string): Promise<LifeGoal> {
  return apiFetch<LifeGoal>(`/goals/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export interface PartnerStatus {
  version?: string
  pending_approvals: number
  open_goals: number
  active_goal?: string | null
  next_action?: string | null
  last_step?: LifeLastStep | null
  life_folder?: string | null
  cas?: { count?: number; kinds?: Record<string, number> } | null
  organism?: {
    alive?: boolean
    mood?: string
    emoji?: string
    label?: string
    life_title?: string
    cas_count?: number
  } | null
  access_scope: string
  harness_mode: string
  brief_intent: string
  /** Focused session used for quality/metabolism counters (multi-tab). */
  session_id?: string | null
  approvals: PendingApproval[]
  provider_health?: ProviderHealthHint
  /** Lean metabolism counters (tier/EU/DU) — never full organ lists. */
  metabolism?: Record<string, unknown>
  /** Soul somatic signal (mood / bond). */
  soma?: SomaStatus
}

export async function getPartnerStatus(
  sessionId?: string | null,
): Promise<PartnerStatus> {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch<PartnerStatus>(`/partner/status${q}`)
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

export type LifeTaskStep = {
  title: string
  status?: string
  observed?: string
  block_reason?: string
}

export type LifeTaskHandoff = {
  kind?: string
  auto?: boolean
  paused_url?: string
}

export type LifeTaskCard = {
  task_id?: string | null
  goal?: string
  status?: string
  ok?: boolean
  spoken?: string
  step?: number
  total?: number
  title?: string
  steps?: LifeTaskStep[]
  approval_id?: string | null
  choices?: string[]
  checkpoint?: boolean
  kind?: string
  session_id?: string | null
  updated_at?: number
  handoff?: LifeTaskHandoff
}

export type LifeTaskCurrent = {
  task: LifeTaskCard | null
  approval?: PendingApproval | null
}

export type LifeTaskActResult = {
  ok?: boolean
  action?: string
  spoken?: string
  task?: LifeTaskCard | null
  result?: { status?: string; task_id?: string }
}

export async function getCurrentLifeTask(
  sessionId?: string | null,
): Promise<LifeTaskCurrent> {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch<LifeTaskCurrent>(`/life-tasks/current${q}`)
}

export async function listLifeTasks(
  sessionId?: string | null,
): Promise<{ id?: string; goal?: string; status?: string }[]> {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  const data = await apiFetch<{ tasks?: { id?: string; goal?: string; status?: string }[] }>(
    `/life-tasks${q}`,
  )
  return data.tasks || []
}

export async function getLifeTask(taskId: string): Promise<Record<string, unknown>> {
  return apiFetch(`/life-tasks/${encodeURIComponent(taskId)}`)
}

export async function probeLifeTask(opts?: {
  sessionId?: string | null
  taskId?: string | null
  pageText?: string
  url?: string
  railReady?: boolean | null
}): Promise<LifeTaskActResult & { cleared?: boolean; resumed?: boolean }> {
  return apiFetch('/life-tasks/probe', {
    method: 'POST',
    body: JSON.stringify({
      session_id: opts?.sessionId || undefined,
      task_id: opts?.taskId || undefined,
      page_text: opts?.pageText || '',
      url: opts?.url || '',
      rail_ready: opts?.railReady ?? null,
    }),
  })
}

export async function actLifeTask(
  action: 'yes' | 'no' | 'explain',
  opts?: { sessionId?: string | null; taskId?: string | null; approvalId?: string | null },
): Promise<LifeTaskActResult> {
  return apiFetch<LifeTaskActResult>('/life-tasks/act', {
    method: 'POST',
    body: JSON.stringify({
      action,
      session_id: opts?.sessionId || undefined,
      task_id: opts?.taskId || undefined,
      approval_id: opts?.approvalId || undefined,
    }),
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

/** @deprecated Prefer `approvePlan` from `./plans` — kept for panel imports. */
export { approvePlan, cancelPlan, setPlanStatus } from './plans'

export async function getSkillReuseMetrics(): Promise<{
  total_activations: number
  skills_with_activation: number
  multi_session_reactivations: number
  skills: { name: string; activations: number }[]
}> {
  return apiFetch('/skills/metrics/reuse')
}
