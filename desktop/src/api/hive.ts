import { apiFetch } from './client'

export interface HiveRosterRow {
  id: string
  cadence: string
  status: string
  goal: string
  done: boolean
  outcome: string
  blockers: string[]
  updated_at: string
  pulse_s: number
  pulse_count?: number
  next_pulse_at?: string
}

export interface HiveRoster {
  daughters: HiveRosterRow[]
  live_posts: number
  live_foragers: number
  count: number
}

export function hiveRowLabel(row: HiveRosterRow): string {
  const goal = (row.goal || '').trim() || 'untitled job'
  const short = goal.length > 72 ? `${goal.slice(0, 71)}…` : goal
  return `${row.cadence} · ${row.status} · ${short}`
}

export function hiveIsLive(row: HiveRosterRow): boolean {
  return row.status !== 'retired' && row.status !== 'cancelled'
}

export async function getHiveRoster(): Promise<HiveRoster> {
  return apiFetch<HiveRoster>('/hive/roster')
}

export async function retireHiveDaughter(hiveId: string): Promise<{ ok: boolean; error?: string }> {
  return apiFetch('/hive/retire', {
    method: 'POST',
    body: JSON.stringify({ hive_id: hiveId }),
  })
}

export interface HiveSpawnResult {
  ok: boolean
  error?: string
  hive_id?: string
  cadence?: string
  status?: string
  started?: boolean
  goal?: string
}

export interface HiveAssignResult {
  ok: boolean
  error?: string
  hive_id?: string
  goal?: string
  status?: string
}

export async function spawnHiveDaughter(params: {
  goal: string
  cadence?: 'forager' | 'post'
  budget_steps?: number
  pulse_s?: number
}): Promise<HiveSpawnResult> {
  return apiFetch<HiveSpawnResult>('/hive/spawn', {
    method: 'POST',
    body: JSON.stringify({
      goal: params.goal,
      cadence: params.cadence ?? 'forager',
      budget_steps: params.budget_steps ?? 8,
      pulse_s: params.pulse_s ?? 0,
    }),
  })
}

export async function assignHiveDaughter(
  hiveId: string,
  goal: string,
): Promise<HiveAssignResult> {
  return apiFetch<HiveAssignResult>('/hive/assign', {
    method: 'POST',
    body: JSON.stringify({ hive_id: hiveId, goal }),
  })
}
