/** Client preference: per-project session order in the sidebar (localStorage). */

import { projectKey } from '../utils/sessionProjects'

const KEY = 'remedy.sessionOrder.v1'

type OrderMap = Record<string, string[]>

function readMap(): OrderMap {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return {}
    const out: OrderMap = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (!Array.isArray(v)) continue
      const pk = projectKey(k) // '' for no-project bucket
      out[pk] = v.filter((id): id is string => typeof id === 'string' && id.length > 0)
    }
    return out
  } catch {
    return {}
  }
}

function writeMap(map: OrderMap) {
  try {
    localStorage.setItem(KEY, JSON.stringify(map))
  } catch {
    /* quota */
  }
}

export function getSessionOrderMap(): OrderMap {
  return readMap()
}

export function getSessionOrderForProject(projectPath: string | null | undefined): string[] {
  const pk = projectKey(projectPath)
  return [...(readMap()[pk] || [])]
}

export function setSessionOrderForProject(
  projectPath: string | null | undefined,
  ids: string[],
): string[] {
  const pk = projectKey(projectPath)
  const map = readMap()
  map[pk] = [...new Set(ids.filter(Boolean))]
  writeMap(map)
  return map[pk]
}

/**
 * Merge saved order with current session ids in a group.
 * Unknown ids append by the order they appear in `currentIds` (caller usually updated_at).
 */
export function mergeSessionOrder(saved: string[], currentIds: string[]): string[] {
  const cur = [...new Set(currentIds.filter(Boolean))]
  const curSet = new Set(cur)
  const ordered = saved.filter((id) => curSet.has(id))
  const seen = new Set(ordered)
  const rest = cur.filter((id) => !seen.has(id))
  return [...ordered, ...rest]
}

export function moveSession(
  sessionId: string,
  projectPath: string | null | undefined,
  dir: 'up' | 'down',
  currentIds: string[],
): string[] {
  if (!sessionId) return mergeSessionOrder(getSessionOrderForProject(projectPath), currentIds)
  const list = mergeSessionOrder(getSessionOrderForProject(projectPath), currentIds)
  const i = list.indexOf(sessionId)
  if (i < 0) return list
  const j = dir === 'up' ? i - 1 : i + 1
  if (j < 0 || j >= list.length) return list
  const next = [...list]
  ;[next[i], next[j]] = [next[j]!, next[i]!]
  return setSessionOrderForProject(projectPath, next)
}

/** When a session changes project (DnD), drop from old list and append to new. */
export function rehomeSessionOrder(
  sessionId: string,
  fromProject: string | null | undefined,
  toProject: string | null | undefined,
): void {
  if (!sessionId) return
  const fromK = projectKey(fromProject)
  const toK = projectKey(toProject)
  if (fromK === toK) return
  const map = readMap()
  map[fromK] = (map[fromK] || []).filter((id) => id !== sessionId)
  const dest = map[toK] || []
  if (!dest.includes(sessionId)) map[toK] = [...dest, sessionId]
  writeMap(map)
}
