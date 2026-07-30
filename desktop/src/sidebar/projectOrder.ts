/** Client preference: order of project folders in the sidebar (localStorage). */

import { isProjectLocked, projectKey } from '../utils/sessionProjects'

const KEY = 'remedy.projectOrder.v1'

function readOrder(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return [
      ...new Set(
        parsed
          .filter((x): x is string => typeof x === 'string' && Boolean(projectKey(x)))
          .map((x) => projectKey(x)),
      ),
    ]
  } catch {
    return []
  }
}

function writeOrder(keys: string[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(keys))
  } catch {
    /* quota */
  }
}

export function getProjectOrder(): string[] {
  return readOrder()
}

export function setProjectOrder(keys: string[]): string[] {
  const next = [
    ...new Set(keys.map((k) => projectKey(k)).filter(Boolean)),
  ]
  writeOrder(next)
  return next
}

/**
 * Merge saved order with currently visible project keys.
 * Unknown keys append in stable locale order.
 */
export function mergeProjectOrder(
  saved: string[],
  activeKeys: string[],
): string[] {
  const active = [
    ...new Set(activeKeys.map((k) => projectKey(k)).filter(Boolean)),
  ]
  const activeSet = new Set(active)
  const ordered = saved.filter((k) => activeSet.has(k))
  const seen = new Set(ordered)
  const rest = active
    .filter((k) => !seen.has(k))
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
  return [...ordered, ...rest]
}

export function moveProject(
  key: string,
  dir: 'up' | 'down',
  activeKeys: string[],
): string[] {
  const k = projectKey(key)
  if (!k) return mergeProjectOrder(readOrder(), activeKeys)
  // Locked project folders keep their place (also blocks swaps into them from neighbors).
  if (isProjectLocked(k)) return mergeProjectOrder(readOrder(), activeKeys)
  const list = mergeProjectOrder(readOrder(), activeKeys)
  const i = list.indexOf(k)
  if (i < 0) return list
  const j = dir === 'up' ? i - 1 : i + 1
  if (j < 0 || j >= list.length) return list
  // Do not swap past another locked folder either — keeps locked slots fixed.
  const neighbor = list[j]!
  if (isProjectLocked(neighbor)) return list
  const next = [...list]
  ;[next[i], next[j]] = [next[j]!, next[i]!]
  return setProjectOrder(next)
}
