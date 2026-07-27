/**
 * Resolve markdown image `src` so chat can display local files regardless of
 * LLM provider. Models often emit `assets/foo.png` or `C:\\...\\bar.png` —
 * WebView cannot load those as bare paths.
 */
import { ensureApiToken, getApiBase, authHeaders } from '../api/client'

/** LRU-ish blob URL cache (path → object URL). Cap avoids unbounded memory. */
const BLOB_CACHE_MAX = 64
const blobCache = new Map<string, string>()

function cacheSet(path: string, objectUrl: string): void {
  // Refresh insertion order for simple LRU: delete then re-set.
  if (blobCache.has(path)) {
    const old = blobCache.get(path)
    blobCache.delete(path)
    if (old && old !== objectUrl) {
      try {
        URL.revokeObjectURL(old)
      } catch {
        /* ignore */
      }
    }
  }
  blobCache.set(path, objectUrl)
  while (blobCache.size > BLOB_CACHE_MAX) {
    const oldest = blobCache.keys().next().value as string | undefined
    if (!oldest) break
    const u = blobCache.get(oldest)
    blobCache.delete(oldest)
    if (u) {
      try {
        URL.revokeObjectURL(u)
      } catch {
        /* ignore */
      }
    }
  }
}

export function isRemoteOrDataUrl(src: string): boolean {
  const s = (src || '').trim()
  return /^(https?:|data:|blob:)/i.test(s)
}

/** WebView-safe: only http(s) may use crossOrigin for canvas export. */
export function shouldUseCorsForImage(src: string): boolean {
  return /^https?:\/\//i.test((src || '').trim())
}

/** True when src looks like a local filesystem / project-relative path. */
export function isLocalMediaPath(src: string): boolean {
  const s = (src || '').trim()
  if (!s || isRemoteOrDataUrl(s)) return false
  if (s.startsWith('file:')) return true
  // Windows drive path
  if (/^[A-Za-z]:[\\/]/.test(s)) return true
  // UNC
  if (s.startsWith('\\\\')) return true
  // Relative project path (assets/…, ./foo, previews/…)
  if (!s.includes('://') && !s.startsWith('//')) return true
  return false
}

export function normalizeLocalMediaPath(src: string): string {
  // Angle-bracket markdown targets: ![alt](<C:/path with space.png>)
  let s = (src || '').trim().replace(/^<|>$/g, '')
  if (s.toLowerCase().startsWith('file:')) {
    s = s.slice(5)
    // file:///C:/Users → strip leading slashes carefully
    while (s.startsWith('/') || s.startsWith('\\')) {
      // Keep drive letter forms: /C:/Users after one strip becomes C:/Users
      if (/^\/[A-Za-z]:/.test(s) || /^\\[A-Za-z]:/.test(s)) {
        s = s.slice(1)
        break
      }
      s = s.slice(1)
      if (/^[A-Za-z]:/.test(s)) break
    }
  }
  try {
    if (/%[0-9A-Fa-f]{2}/.test(s)) s = decodeURIComponent(s)
  } catch {
    /* keep raw */
  }
  return s
}

/**
 * Return a browser-loadable URL for an image src.
 * Remote/data/blob URLs pass through; local paths become authenticated blob URLs.
 */
export async function resolveChatMediaUrl(src: string): Promise<string> {
  const raw = (src || '').trim()
  if (!raw) return ''
  // Reject protocol-relative and non-http(s)/data/blob remotes.
  if (/^\/\//.test(raw)) return ''
  if (isRemoteOrDataUrl(raw)) {
    if (/^(javascript|vbscript|file):/i.test(raw)) return ''
    return raw
  }
  if (!isLocalMediaPath(raw)) return ''

  const path = normalizeLocalMediaPath(raw)
  const cached = blobCache.get(path)
  if (cached) {
    // Touch for LRU order
    blobCache.delete(path)
    blobCache.set(path, cached)
    return cached
  }

  await ensureApiToken()
  const url = `${getApiBase()}/media?path=${encodeURIComponent(path)}`
  const res = await fetch(url, {
    headers: {
      Accept: 'image/*,*/*',
      ...authHeaders(),
    },
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`media ${res.status}: ${detail || res.statusText}`)
  }
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  cacheSet(path, objectUrl)
  return objectUrl
}

/** Drop a cached blob (e.g. after long sessions) — optional. */
export function clearChatMediaCache(): void {
  for (const u of blobCache.values()) {
    try {
      URL.revokeObjectURL(u)
    } catch {
      /* ignore */
    }
  }
  blobCache.clear()
}
