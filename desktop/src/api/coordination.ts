/** Body coordination — the live muscles (sessions) and the files they hold. */
import { apiFetch } from './client'

export interface CoordinationBeacon {
  session_id: string
  /** True when this beacon is the caller's own session. */
  you: boolean
  /** Provider/model label, e.g. "xai/grok-4.5". */
  muscle: string
  /** Project folder name (basename) for compact display. */
  project: string
  project_path: string
  goal: string
  phase: string
  held_files: string[]
  held_count: number
  age_seconds: number
  heartbeat_seconds_ago: number
}

export interface CoordinationPresence {
  beacons: CoordinationBeacon[]
  count: number
  ts: number
}

export async function getCoordinationPresence(
  sessionId?: string | null,
): Promise<CoordinationPresence> {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch<CoordinationPresence>(`/coordination/presence${q}`)
}
