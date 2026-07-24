/** Local session extras: pin + tags (no server schema required). */

export type SessionMeta = {
  pinned?: boolean
  tags?: string[]
  /** Optional folder/label group */
  folder?: string
}

const KEY = 'remedy.sessionMeta.v1'

function readAll(): Record<string, SessionMeta> {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return {}
    return parsed as Record<string, SessionMeta>
  } catch {
    return {}
  }
}

function writeAll(map: Record<string, SessionMeta>) {
  try {
    localStorage.setItem(KEY, JSON.stringify(map))
  } catch {
    /* quota */
  }
}

export function getSessionMeta(id: string): SessionMeta {
  return readAll()[id] || {}
}

export function getAllSessionMeta(): Record<string, SessionMeta> {
  return readAll()
}

export function setSessionMeta(id: string, patch: Partial<SessionMeta>): SessionMeta {
  const all = readAll()
  const next = { ...(all[id] || {}), ...patch }
  // Normalize tags
  if (next.tags) {
    next.tags = [...new Set(next.tags.map((t) => t.trim()).filter(Boolean))].slice(0, 12)
  }
  if (next.folder !== undefined) {
    next.folder = String(next.folder || '').trim().slice(0, 40)
  }
  all[id] = next
  writeAll(all)
  return next
}

export function toggleSessionPin(id: string): boolean {
  const cur = getSessionMeta(id)
  const pinned = !cur.pinned
  setSessionMeta(id, { pinned })
  return pinned
}

export function removeSessionMeta(id: string) {
  const all = readAll()
  delete all[id]
  writeAll(all)
}
