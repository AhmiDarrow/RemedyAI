/** Remedy's head — children, routines, heartbeat, goal autopilot.
 *
 * Remedy is one partner. She keeps her own conversation (the head) and
 * starts children for side jobs; they report back to her. Everything here
 * runs through one queue sized to this PC (`capacity`). */

import { apiFetch } from './client'

export type HeadState =
  | 'idle'
  | 'queued'
  | 'working'
  | 'waiting'
  | 'blocked'
  | 'done'
  | 'stopped'
  | 'error'

export interface HeadCapacity {
  slots: number
  busy: number
  queued: number
  local_model: boolean
  owner_busy: boolean
  memory_known: boolean
  free_mem_pct: number
  free_mem_gb?: string
  cpus: number
  paused: boolean
  reason: string
}

export interface HeadChild {
  child_id: string
  session_id: string
  parent_session_id?: string
  label: string
  task: string
  state: HeadState
  last_report?: string
  goal_id?: string
  started_at?: string
  updated_at?: string
  runs?: number
}

export interface HeadSchedule {
  kind: 'every' | 'daily' | 'weekdays' | 'weekly' | 'once'
  every_minutes?: number
  at?: string
  days?: string[]
  once_at?: string
}

export interface HeadRoutine {
  routine_id: string
  title: string
  what: string
  mode: 'remind' | 'task'
  when: string
  schedule: HeadSchedule
  target: string
  deliver: 'grove' | 'messenger' | 'both'
  enabled: boolean
  next_run?: string
  last_run?: string
  last_state?: HeadState | ''
  last_summary?: string
  runs: number
}

export interface HeadGoalLink {
  goal_id: string
  session_id?: string
  autopilot: boolean
  every_minutes?: number
  state?: HeadState | ''
  last_update?: string
  last_run_at?: string
  runs?: number
}

export interface HeadActivity {
  id: string
  ts: string
  kind: 'child' | 'routine' | 'heartbeat' | 'autopilot' | 'note' | 'messenger' | 'system'
  title: string
  detail?: string
  state?: HeadState | ''
  session_id?: string
  goal_id?: string
  routine_id?: string
  channel?: string
}

export interface HeadChannel {
  channel: string
  chat_id: string
  username?: string
  last_at?: string
}

export interface HeadOverview {
  head: { session_id: string; state: HeadState }
  capacity: HeadCapacity
  capacity_note: string
  children: HeadChild[]
  goals: Record<string, HeadGoalLink>
  routines: HeadRoutine[]
  queue: { kind: string; label: string; session_id: string; queued_at: string }[]
  activity: HeadActivity[]
  activity_unseen: number
  heartbeat: {
    enabled: boolean
    every_minutes: number
    quiet_start: string
    quiet_end: string
    last_at?: string
    last_note?: string
  }
  channels: { owner: HeadChannel | null; recent: HeadChannel[] }
}

export function getHead(): Promise<HeadOverview> {
  return apiFetch<HeadOverview>('/partner/head')
}

/** Remedy's own conversation; adopts `sessionId` only when she has none. */
export async function ensureHeadSession(sessionId?: string): Promise<string> {
  const r = await apiFetch<{ session_id: string }>('/partner/head/session', {
    method: 'POST',
    body: JSON.stringify(sessionId ? { session_id: sessionId } : {}),
  })
  return r.session_id
}

export function setHeartbeat(patch: {
  enabled?: boolean
  every_minutes?: number
  quiet_start?: string
  quiet_end?: string
}): Promise<unknown> {
  return apiFetch('/partner/heartbeat', { method: 'PUT', body: JSON.stringify(patch) })
}

export function runHeartbeatNow(): Promise<{ note: string; changed: boolean }> {
  return apiFetch('/partner/heartbeat/run', { method: 'POST', body: '{}' })
}

export function createRoutine(body: {
  title?: string
  what: string
  mode: 'remind' | 'task'
  schedule: HeadSchedule
  target?: string
  deliver?: 'grove' | 'messenger' | 'both'
}): Promise<{ routine: HeadRoutine }> {
  return apiFetch('/partner/routines', { method: 'POST', body: JSON.stringify(body) })
}

export function patchRoutine(
  id: string,
  patch: Partial<{ enabled: boolean; title: string; what: string; schedule: HeadSchedule }>,
): Promise<{ routine: HeadRoutine }> {
  return apiFetch(`/partner/routines/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function deleteRoutine(id: string): Promise<unknown> {
  return apiFetch(`/partner/routines/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function runRoutineNow(id: string): Promise<unknown> {
  return apiFetch(`/partner/routines/${encodeURIComponent(id)}/run`, { method: 'POST', body: '{}' })
}

export function setGoalLink(
  goalId: string,
  body: { session_id?: string; autopilot?: boolean; every_minutes?: number },
): Promise<{ link: HeadGoalLink }> {
  return apiFetch(`/partner/goals/${encodeURIComponent(goalId)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function stepGoalNow(goalId: string): Promise<{ session_id: string; capacity_note: string }> {
  return apiFetch(`/partner/goals/${encodeURIComponent(goalId)}/step`, { method: 'POST', body: '{}' })
}

export function spawnChild(body: {
  task: string
  label?: string
  goal_id?: string
}): Promise<{ child: HeadChild; capacity_note: string }> {
  return apiFetch('/partner/children', { method: 'POST', body: JSON.stringify(body) })
}

export function stopChild(id: string): Promise<unknown> {
  return apiFetch(`/partner/children/${encodeURIComponent(id)}/stop`, { method: 'POST', body: '{}' })
}

export function forgetChild(id: string): Promise<unknown> {
  return apiFetch(`/partner/children/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function setOwnerChannel(ch: HeadChannel | null): Promise<unknown> {
  return apiFetch('/partner/channel', {
    method: 'PUT',
    body: JSON.stringify(ch ? { channel: ch.channel, chat_id: ch.chat_id } : {}),
  })
}

export function markActivitySeen(): Promise<unknown> {
  return apiFetch('/partner/activity/seen', { method: 'POST', body: '{}' })
}

// ── presentation helpers (pure; tested) ─────────────────────────────────

/** Plain words for a state, as the owner reads them. */
export function stateWords(state: HeadState | '' | undefined): string {
  switch (state) {
    case 'working':
      return 'Working'
    case 'queued':
      return 'Waiting for room'
    case 'waiting':
    case 'blocked':
      return 'Needs you'
    case 'done':
      return 'Done'
    case 'stopped':
      return 'Stopped'
    case 'error':
      return 'Hit a problem'
    default:
      return 'Resting'
  }
}

/** A state that wants the owner's eyes. */
export function needsOwner(state: HeadState | '' | undefined): boolean {
  return state === 'waiting' || state === 'blocked' || state === 'error'
}

/** "in 3h", "in 12m", "now" — relative to `now` (ms). */
export function relativeWhen(iso: string | undefined, now: number = Date.now()): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const diff = t - now
  const abs = Math.abs(diff)
  const mins = Math.round(abs / 60_000)
  let span: string
  if (mins < 1) return diff >= 0 ? 'now' : 'just now'
  if (mins < 60) span = `${mins}m`
  else if (mins < 60 * 24) span = `${Math.round(mins / 60)}h`
  else span = `${Math.round(mins / (60 * 24))}d`
  return diff >= 0 ? `in ${span}` : `${span} ago`
}

/** Build a schedule from the routine dialog's simple choices. */
export function scheduleFromChoice(
  choice: 'hourly' | 'every' | 'daily' | 'weekdays' | 'weekly' | 'once',
  opts: { at?: string; everyHours?: number; days?: string[]; onceLocal?: string },
): HeadSchedule {
  switch (choice) {
    case 'hourly':
      return { kind: 'every', every_minutes: 60 }
    case 'every':
      return { kind: 'every', every_minutes: Math.max(1, Math.round(opts.everyHours || 1)) * 60 }
    case 'daily':
      return { kind: 'daily', at: opts.at || '08:00' }
    case 'weekdays':
      return { kind: 'weekdays', at: opts.at || '08:00' }
    case 'weekly':
      return { kind: 'weekly', at: opts.at || '08:00', days: opts.days?.length ? opts.days : ['mon'] }
    case 'once': {
      const d = opts.onceLocal ? new Date(opts.onceLocal) : new Date(Date.now() + 3_600_000)
      return { kind: 'once', once_at: d.toISOString().replace(/\.\d{3}Z$/, 'Z') }
    }
  }
}
