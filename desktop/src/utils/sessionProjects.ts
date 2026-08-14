/** Group sessions by project_path and track known empty project folders. */

import type { ChatSession } from '../types'

const KNOWN_KEY = 'remedy.knownProjects.v1'
const COLLAPSE_KEY = 'remedy.projectCollapse.v1'
/** Project folder keys the user locked (no reorder / remove from sidebar). */
const LOCKED_KEY = 'remedy.projectLocked.v1'

/** Drive / FS root — not a project folder (sidebar used to grow a ``C:`` bucket). */
export function isVolumeRootPath(raw: string | null | undefined): boolean {
  const t = (raw || '').trim()
  if (!t) return false
  if (t === '/' || t === '\\') return true
  return /^[a-zA-Z]:[\\/]*$/.test(t)
}

/** True when session has no real project folder. */
export function isNoProjectPath(raw: string | null | undefined): boolean {
  const t = (raw || '').trim()
  return !t || t === '.' || t === './' || isVolumeRootPath(t)
}

/** Stable key for grouping (normalized absolute-ish path). */
export function projectKey(raw: string | null | undefined): string {
  if (isNoProjectPath(raw)) return ''
  let p = String(raw).trim().replace(/\//g, '\\')
  // Drop trailing slashes
  while (p.length > 3 && (p.endsWith('\\') || p.endsWith('/'))) {
    p = p.slice(0, -1)
  }
  // Case-normalize drive letter on Windows
  if (/^[a-zA-Z]:/.test(p)) {
    p = p[0]!.toUpperCase() + p.slice(1)
  }
  return p
}

/** Short label for sidebar folder headers. */
export function projectDisplayName(path: string): string {
  if (isNoProjectPath(path)) return 'No project'
  const norm = projectKey(path)
  const parts = norm.split(/[/\\]/).filter(Boolean)
  const last = parts[parts.length - 1] || norm
  return last
}

export type ProjectGroup = {
  /** Empty string = no project */
  key: string
  /** Full path (empty for no-project) */
  path: string
  label: string
  sessions: ChatSession[]
}

/** Known project folders the user added (may have zero sessions yet). */
export function getKnownProjects(): string[] {
  try {
    const raw = localStorage.getItem(KNOWN_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return [
      ...new Set(
        parsed
          .filter((x): x is string => typeof x === 'string' && !isNoProjectPath(x))
          .map((x) => projectKey(x)),
      ),
    ].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
  } catch {
    return []
  }
}

export function addKnownProject(path: string): string[] {
  const key = projectKey(path)
  if (!key) return getKnownProjects()
  const next = [...new Set([...getKnownProjects(), key])].sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: 'base' }),
  )
  try {
    localStorage.setItem(KNOWN_KEY, JSON.stringify(next))
  } catch {
    /* quota */
  }
  return next
}

export function removeKnownProject(path: string): string[] {
  const key = projectKey(path)
  if (isProjectLocked(key)) {
    // Hard guard: locked folders cannot be removed from the sidebar.
    return getKnownProjects()
  }
  const next = getKnownProjects().filter((p) => p !== key)
  try {
    localStorage.setItem(KNOWN_KEY, JSON.stringify(next))
  } catch {
    /* */
  }
  // Drop lock entry if present (defensive; locked path never reaches here).
  setProjectLocked(key, false)
  return next
}

/** Set of locked project keys (normalized via projectKey). */
export function getLockedProjects(): Set<string> {
  try {
    const raw = localStorage.getItem(LOCKED_KEY)
    if (!raw) return new Set()
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return new Set()
    return new Set(
      parsed
        .filter((x): x is string => typeof x === 'string' && !isNoProjectPath(x))
        .map((x) => projectKey(x))
        .filter(Boolean),
    )
  } catch {
    return new Set()
  }
}

function writeLockedProjects(keys: Set<string>): void {
  try {
    localStorage.setItem(LOCKED_KEY, JSON.stringify([...keys].sort()))
  } catch {
    /* quota */
  }
}

export function isProjectLocked(path: string | null | undefined): boolean {
  const key = projectKey(path)
  if (!key) return false
  return getLockedProjects().has(key)
}

export function setProjectLocked(path: string, locked: boolean): Set<string> {
  const key = projectKey(path)
  if (!key) return getLockedProjects()
  const next = getLockedProjects()
  if (locked) next.add(key)
  else next.delete(key)
  writeLockedProjects(next)
  return next
}

export function toggleProjectLocked(path: string): Set<string> {
  const key = projectKey(path)
  if (!key) return getLockedProjects()
  return setProjectLocked(key, !isProjectLocked(key))
}

export function getCollapsedProjects(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(COLLAPSE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return {}
    return parsed as Record<string, boolean>
  } catch {
    return {}
  }
}

export function setProjectCollapsed(key: string, collapsed: boolean) {
  const all = getCollapsedProjects()
  if (collapsed) all[key] = true
  else delete all[key]
  try {
    localStorage.setItem(COLLAPSE_KEY, JSON.stringify(all))
  } catch {
    /* */
  }
}

/**
 * Build tree groups: "No project" first, then project folders (with sessions
 * and/or known empty projects).
 */
export function groupSessionsByProject(
  sessions: ChatSession[],
  knownProjects: string[] = getKnownProjects(),
): ProjectGroup[] {
  const byKey = new Map<string, ChatSession[]>()
  for (const s of sessions) {
    const key = projectKey(s.project_path)
    const list = byKey.get(key) || []
    list.push(s)
    byKey.set(key, list)
  }

  const sortSessions = (list: ChatSession[]) =>
    [...list].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))

  const groups: ProjectGroup[] = []

  // No project always first
  groups.push({
    key: '',
    path: '',
    label: 'No project',
    sessions: sortSessions(byKey.get('') || []),
  })
  byKey.delete('')

  const keys = new Set<string>([...byKey.keys(), ...knownProjects.map(projectKey)])
  const projectKeys = [...keys]
    .filter(Boolean)
    .sort((a, b) => {
      const la = projectDisplayName(a).toLowerCase()
      const lb = projectDisplayName(b).toLowerCase()
      if (la !== lb) return la.localeCompare(lb)
      return a.localeCompare(b)
    })

  for (const key of projectKeys) {
    groups.push({
      key,
      path: key,
      label: projectDisplayName(key),
      sessions: sortSessions(byKey.get(key) || []),
    })
  }

  return groups
}
