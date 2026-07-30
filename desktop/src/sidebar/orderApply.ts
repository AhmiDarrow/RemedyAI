/** Pure helpers: apply saved order arrays onto project groups / session lists. */

import type { ChatSession } from '../types'
import type { ProjectGroup } from '../utils/sessionProjects'
import { mergeProjectOrder } from './projectOrder'
import { mergeSessionOrder } from './sessionOrder'

/** Synthetic group key for the top-of-list pinned strip (not a real project path). */
export const PINNED_GROUP_KEY = '__pinned__'

/**
 * Reorder project groups.
 * Order: locked projects (saved order among them) → "No project" → unlocked projects.
 */
export function applyProjectOrder(
  groups: ProjectGroup[],
  orderKeys: string[],
  lockedKeys: Set<string> = new Set(),
): ProjectGroup[] {
  if (!groups.length) return groups
  const none = groups.find((g) => !g.key)
  const projects = groups.filter((g) => g.key && g.key !== PINNED_GROUP_KEY)
  if (!projects.length) {
    return none ? [none] : groups.filter((g) => g.key === PINNED_GROUP_KEY)
  }

  const activeKeys = projects.map((g) => g.key)
  const orderedKeys = mergeProjectOrder(orderKeys, activeKeys)
  const byKey = new Map(projects.map((g) => [g.key, g]))

  const lockedOrdered: ProjectGroup[] = []
  const unlockedOrdered: ProjectGroup[] = []
  for (const k of orderedKeys) {
    const g = byKey.get(k)
    if (!g) continue
    if (lockedKeys.has(k)) lockedOrdered.push(g)
    else unlockedOrdered.push(g)
  }
  // Safety: any missed groups
  for (const g of projects) {
    if (orderedKeys.includes(g.key)) continue
    if (lockedKeys.has(g.key)) lockedOrdered.push(g)
    else unlockedOrdered.push(g)
  }

  const out: ProjectGroup[] = [...lockedOrdered]
  if (none) out.push(none)
  out.push(...unlockedOrdered)
  return out
}

/**
 * Sort sessions: pinned first (updated_at among pinned), then manual order,
 * then remaining by updated_at desc.
 */
export function applySessionOrder(
  sessions: ChatSession[],
  orderIds: string[],
  pinnedIds: Set<string>,
  /** When true, strip pinned sessions out (they live in the top ★ Pinned strip). */
  excludePinned = false,
): ChatSession[] {
  if (!sessions.length) return sessions
  const byId = new Map(sessions.map((s) => [s.id, s]))
  const pinned = sessions
    .filter((s) => pinnedIds.has(s.id))
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  const unpinned = sessions.filter((s) => !pinnedIds.has(s.id))
  if (sessions.length <= 1 && !excludePinned) return sessions
  const unpinnedIds = unpinned.map((s) => s.id)
  const orderedIds = mergeSessionOrder(orderIds, unpinnedIds)
  const rest: ChatSession[] = []
  for (const id of orderedIds) {
    const s = byId.get(id)
    if (s && !pinnedIds.has(id)) rest.push(s)
  }
  if (excludePinned) return rest
  return [...pinned, ...rest]
}

export type ApplySidebarOrderOpts = {
  /** Locked project keys float to the top of the Sessions list. */
  lockedKeys?: Set<string>
  /**
   * When true (default), pinned sessions appear only in a synthetic ★ Pinned
   * group at the top — not duplicated under their project folder.
   */
  pinStrip?: boolean
}

/** Apply project + per-group session order to full group list. */
export function applySidebarOrder(
  groups: ProjectGroup[],
  projectOrderKeys: string[],
  sessionOrderByProject: Record<string, string[]>,
  pinnedIds: Set<string>,
  opts?: ApplySidebarOrderOpts,
): ProjectGroup[] {
  const lockedKeys = opts?.lockedKeys ?? new Set<string>()
  const pinStrip = opts?.pinStrip !== false

  // Collect pinned sessions across all real groups for the top strip.
  const allPinned: ChatSession[] = []
  if (pinStrip && pinnedIds.size) {
    for (const g of groups) {
      for (const s of g.sessions) {
        if (pinnedIds.has(s.id)) allPinned.push(s)
      }
    }
    allPinned.sort((a, b) =>
      (b.updated_at || '').localeCompare(a.updated_at || ''),
    )
  }

  const withSessions = groups
    .filter((g) => g.key !== PINNED_GROUP_KEY)
    .map((g) => ({
      ...g,
      sessions: applySessionOrder(
        g.sessions,
        sessionOrderByProject[g.key] || [],
        pinnedIds,
        pinStrip && pinnedIds.size > 0,
      ),
    }))

  const ordered = applyProjectOrder(withSessions, projectOrderKeys, lockedKeys)

  if (!pinStrip || !allPinned.length) return ordered

  const pinGroup: ProjectGroup = {
    key: PINNED_GROUP_KEY,
    path: '',
    label: '★ Pinned',
    sessions: allPinned,
  }
  return [pinGroup, ...ordered]
}
