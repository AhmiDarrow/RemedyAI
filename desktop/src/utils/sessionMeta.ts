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
  pushFiling(id, next)
  return next
}

/**
 * How a session is filed (pinned, archived, tags) is kept by the runtime, so
 * the desktop, this page and a phone show the same. localStorage is the copy
 * this page paints from. `folder` is this page's own and stays local.
 */
const SYNCED_KEY = 'remedy.sessionMeta.synced'

function readSynced(): Set<string> {
  try {
    const raw = localStorage.getItem(SYNCED_KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : []
    return new Set(Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}

function writeSynced(ids: Set<string>) {
  try {
    localStorage.setItem(SYNCED_KEY, JSON.stringify([...ids]))
  } catch {
    // ignore
  }
}

function pushFiling(id: string, m: SessionMeta) {
  const body = JSON.stringify({
    pinned: Boolean(m.pinned),
    archived: Boolean(m.archived),
    tags: m.tags || [],
  })
  // Imported on use: this module is also loaded where there is no runtime to reach.
  import('../api/client')
    .then(({ apiFetch }) => apiFetch(`/sessions/${id}`, { method: 'PATCH', body }))
    .then(() => {
      const synced = readSynced()
      synced.add(id)
      writeSynced(synced)
    })
    .catch(() => {
      // Kept locally; sent again the next time it changes.
    })
}

export type ServerFiling = { id: string; pinned?: boolean; archived?: boolean; tags?: string[] | null }

/**
 * Bring this page in line with the runtime's filing for the sessions it just
 * listed. Returns true when the local copy changed.
 *
 * A session filed here before the runtime kept filing is sent up once. After
 * that the runtime is the truth, including when a pin was removed elsewhere.
 */
export function adoptServerFiling(rows: ServerFiling[]): boolean {
  const all = readAll()
  const synced = readSynced()
  let changed = false
  let marked = false
  for (const row of rows) {
    const tags = (row.tags || []).filter(Boolean)
    const filed = Boolean(row.pinned) || Boolean(row.archived) || tags.length > 0
    const cur = all[row.id] || {}
    const localFiled = Boolean(cur.pinned) || Boolean(cur.archived) || (cur.tags || []).length > 0
    if (!filed && localFiled && !synced.has(row.id)) {
      pushFiling(row.id, cur)
      continue
    }
    const same =
      Boolean(cur.pinned) === Boolean(row.pinned) &&
      Boolean(cur.archived) === Boolean(row.archived) &&
      JSON.stringify([...(cur.tags || [])].sort()) === JSON.stringify([...tags].sort())
    if (!same) {
      all[row.id] = { ...cur, pinned: Boolean(row.pinned), archived: Boolean(row.archived), tags }
      changed = true
    }
    if (!synced.has(row.id)) {
      synced.add(row.id)
      marked = true
    }
  }
  if (changed) writeAll(all)
  if (marked) writeSynced(synced)
  return changed
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
