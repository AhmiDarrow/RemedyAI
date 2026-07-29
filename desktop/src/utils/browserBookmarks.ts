/**
 * Simple local bookmarks for the in-app Browser (not a full browser profile).
 * Stored in localStorage — survives restarts; no cloud sync.
 */
import { normalizeBrowserUrl } from './browserUrl'

const KEY = 'remedy.browserBookmarks.v1'
const MAX = 80

export type BrowserBookmark = {
  id: string
  title: string
  url: string
  createdAt: number
}

function safeParse(raw: string | null): BrowserBookmark[] {
  if (!raw) return []
  try {
    const v = JSON.parse(raw) as unknown
    if (!Array.isArray(v)) return []
    const out: BrowserBookmark[] = []
    for (const item of v) {
      if (!item || typeof item !== 'object') continue
      const o = item as Record<string, unknown>
      const url = normalizeBrowserUrl(String(o.url || ''))
      if (!url || url.startsWith('about:')) continue
      const id = typeof o.id === 'string' && o.id ? o.id : `bm_${out.length}`
      const title =
        typeof o.title === 'string' && o.title.trim()
          ? o.title.trim().slice(0, 120)
          : titleFromUrl(url)
      const createdAt =
        typeof o.createdAt === 'number' && Number.isFinite(o.createdAt)
          ? o.createdAt
          : Date.now()
      out.push({ id, title, url, createdAt })
      if (out.length >= MAX) break
    }
    return out
  } catch {
    return []
  }
}

export function titleFromUrl(url: string): string {
  try {
    const u = new URL(url)
    return u.hostname.replace(/^www\./, '') + (u.pathname === '/' ? '' : u.pathname)
  } catch {
    return url.slice(0, 60)
  }
}

export function loadBookmarks(): BrowserBookmark[] {
  try {
    return safeParse(localStorage.getItem(KEY))
  } catch {
    return []
  }
}

function saveBookmarks(list: BrowserBookmark[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)))
  } catch {
    /* quota / private mode */
  }
}

export function isBookmarked(url: string, list?: BrowserBookmark[]): boolean {
  const u = normalizeBrowserUrl(url)
  if (!u) return false
  const items = list ?? loadBookmarks()
  return items.some((b) => b.url === u)
}

/** Add or update bookmark for url. Returns new list. */
export function addBookmark(url: string, title?: string): BrowserBookmark[] {
  const u = normalizeBrowserUrl(url)
  if (!u || u.startsWith('about:')) return loadBookmarks()
  const list = loadBookmarks().filter((b) => b.url !== u)
  const next: BrowserBookmark = {
    id: `bm_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
    title: (title || '').trim().slice(0, 120) || titleFromUrl(u),
    url: u,
    createdAt: Date.now(),
  }
  const out = [next, ...list].slice(0, MAX)
  saveBookmarks(out)
  return out
}

/** Remove by url or id. Returns new list. */
export function removeBookmark(urlOrId: string): BrowserBookmark[] {
  const u = normalizeBrowserUrl(urlOrId)
  const list = loadBookmarks().filter(
    (b) => b.id !== urlOrId && b.url !== urlOrId && !(u && b.url === u),
  )
  saveBookmarks(list)
  return list
}

export function toggleBookmark(url: string, title?: string): {
  list: BrowserBookmark[]
  added: boolean
} {
  const u = normalizeBrowserUrl(url)
  if (!u) return { list: loadBookmarks(), added: false }
  if (isBookmarked(u)) {
    return { list: removeBookmark(u), added: false }
  }
  return { list: addBookmark(u, title), added: true }
}
