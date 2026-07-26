/** Local session extras: pin + tags + archive (no server schema required). */

export type SessionMeta = {
  pinned?: boolean
  tags?: string[]
  /** Optional folder/label group */
  folder?: string
  /** Soft-archive: hidden from hot list unless "Show archived" */
  archived?: boolean
}

/** Auto-archive sessions older than this many days (not pinned / not open). */
export const DEFAULT_AUTO_ARCHIVE_DAYS = 30

const ARCHIVE_DAYS_KEY = 'remedy.autoArchiveDays'

export function getAutoArchiveDays(): number {
  try {
    // getItem missing → null; Number(null) is 0 — must not treat as "0 days".
    const raw = localStorage.getItem(ARCHIVE_DAYS_KEY)
    if (raw == null || raw === '') return DEFAULT_AUTO_ARCHIVE_DAYS
    const n = Number(raw)
    if (Number.isFinite(n) && n >= 0) return Math.min(3650, Math.floor(n))
  } catch {
    /* */
  }
  return DEFAULT_AUTO_ARCHIVE_DAYS
}

export function setAutoArchiveDays(days: number) {
  try {
    localStorage.setItem(ARCHIVE_DAYS_KEY, String(Math.max(0, Math.floor(days))))
  } catch {
    /* */
  }
}

/** True if session should be treated as archived (manual flag or age rule). */
export function isSessionArchived(
  session: { id: string; updated_at?: string },
  meta: SessionMeta | undefined,
  opts?: { openIds?: Set<string>; days?: number },
): boolean {
  if (meta?.pinned) return false
  if (meta?.archived) return true
  if (opts?.openIds?.has(session.id)) return false
  const days = opts?.days ?? getAutoArchiveDays()
  if (days <= 0) return false
  const updated = session.updated_at ? Date.parse(session.updated_at) : NaN
  if (!Number.isFinite(updated)) return false
  const ageMs = Date.now() - updated
  return ageMs > days * 24 * 60 * 60 * 1000
}

export function toggleSessionArchive(id: string): boolean {
  const cur = getSessionMeta(id)
  const archived = !cur.archived
  setSessionMeta(id, { archived })
  return archived
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
  // Pinning clears archive so the session reappears in the hot list
  setSessionMeta(id, { pinned, archived: pinned ? false : cur.archived })
  return pinned
}

export function removeSessionMeta(id: string) {
  const all = readAll()
  delete all[id]
  writeAll(all)
}
