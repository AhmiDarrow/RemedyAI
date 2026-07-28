/** Pure helpers: apply saved order arrays onto project groups / session lists. */

import type { ChatSession } from '../types'
import type { ProjectGroup } from '../utils/sessionProjects'
import { mergeProjectOrder } from './projectOrder'
import { mergeSessionOrder } from './sessionOrder'

/**
 * Reorder project groups. "No project" (key '') always stays first.
 */
export function applyProjectOrder(
  groups: ProjectGroup[],
  orderKeys: string[],
): ProjectGroup[] {
  if (!groups.length) return groups
  const none = groups.find((g) => !g.key)
  const projects = groups.filter((g) => g.key)
  if (!projects.length) return groups

  const activeKeys = projects.map((g) => g.key)
  const orderedKeys = mergeProjectOrder(orderKeys, activeKeys)
  const byKey = new Map(projects.map((g) => [g.key, g]))
  const ordered: ProjectGroup[] = []
  for (const k of orderedKeys) {
    const g = byKey.get(k)
    if (g) ordered.push(g)
  }
  // Safety: any missed groups
  for (const g of projects) {
    if (!orderedKeys.includes(g.key)) ordered.push(g)
  }

  if (none) return [none, ...ordered]
  return ordered
}

/**
 * Sort sessions: pinned first (updated_at among pinned), then manual order,
 * then remaining by updated_at desc.
 */
export function applySessionOrder(
  sessions: ChatSession[],
  orderIds: string[],
  pinnedIds: Set<string>,
): ChatSession[] {
  if (sessions.length <= 1) return sessions
  const byId = new Map(sessions.map((s) => [s.id, s]))
  const pinned = sessions
    .filter((s) => pinnedIds.has(s.id))
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  const unpinned = sessions.filter((s) => !pinnedIds.has(s.id))
  const unpinnedIds = unpinned.map((s) => s.id)
  const orderedIds = mergeSessionOrder(orderIds, unpinnedIds)
  const rest: ChatSession[] = []
  for (const id of orderedIds) {
    const s = byId.get(id)
    if (s && !pinnedIds.has(id)) rest.push(s)
  }
  return [...pinned, ...rest]
}

/** Apply project + per-group session order to full group list. */
export function applySidebarOrder(
  groups: ProjectGroup[],
  projectOrderKeys: string[],
  sessionOrderByProject: Record<string, string[]>,
  pinnedIds: Set<string>,
): ProjectGroup[] {
  const withSessions = groups.map((g) => ({
    ...g,
    sessions: applySessionOrder(
      g.sessions,
      sessionOrderByProject[g.key] || [],
      pinnedIds,
    ),
  }))
  return applyProjectOrder(withSessions, projectOrderKeys)
}
