/**
 * Resolve markdown image `src` so chat can display local files regardless of
 * LLM provider. Models often emit `assets/foo.png` or `C:\\...\\bar.png` —
 * WebView cannot load those as bare paths. Loopback `/api/...` attachment
 * URLs also need Bearer auth (raw <img src> gets 401).
 */
import { ensureApiToken, getApiBase, authHeaders, getServerUrl } from '../api/client'

/** LRU-ish blob URL cache (path → object URL). Cap avoids unbounded memory. */
const BLOB_CACHE_MAX = 96
const blobCache = new Map<string, string>()

function cacheSet(path: string, objectUrl: string): void {
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

/**
 * Loopback Remedy API media/attachment URLs — require Authorization; cannot be
 * used as bare <img src>.
 */
export function isAuthenticatedApiUrl(src: string): boolean {
  const s = (src || '').trim()
  if (!s) return false
  // Same-origin relative API
  if (s.startsWith('/api/')) return true
  try {
    const u = new URL(s, 'http://127.0.0.1')
    const host = (u.hostname || '').toLowerCase()
    const loopback =
      host === '127.0.0.1' || host === 'localhost' || host === '::1'
    if (!loopback) return false
    return u.pathname.startsWith('/api/')
  } catch {
    return false
  }
}

/** True when src looks like a local filesystem / project-relative path. */
export function isLocalMediaPath(src: string): boolean {
  const s = normalizeLocalMediaPath(src)
  if (!s || isRemoteOrDataUrl(s)) return false
  if (isAuthenticatedApiUrl(s)) return false
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
    while (s.startsWith('/') || s.startsWith('\\')) {
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

function cacheGet(key: string): string | null {
  const cached = blobCache.get(key)
  if (!cached) return null
  blobCache.delete(key)
  blobCache.set(key, cached)
  return cached
}

async function fetchAuthedBlob(url: string, cacheKey: string): Promise<string> {
  const hit = cacheGet(cacheKey)
  if (hit) return hit

  const tok = await ensureApiToken()
  if (!tok) {
    throw new Error('media auth: no API token (restart Desktop / check bootstrap)')
  }
  const res = await fetch(url, {
    headers: {
      Accept: 'image/*,*/*',
      ...authHeaders(),
      // Belt-and-suspenders: authHeaders can race empty if token was just set
      Authorization: `Bearer ${tok}`,
    },
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`media ${res.status}: ${detail || res.statusText}`)
  }
  const blob = await res.blob()
  // Reject HTML error pages masquerading as images
  const ct = (res.headers.get('content-type') || blob.type || '').toLowerCase()
  if (ct.includes('text/html') || ct.includes('application/json')) {
    throw new Error(`media not an image (${ct || 'unknown type'})`)
  }
  const objectUrl = URL.createObjectURL(blob)
  cacheSet(cacheKey, objectUrl)
  return objectUrl
}

/**
 * Return a browser-loadable URL for an image src.
 * Remote/data/blob pass through; local paths and loopback /api/* become
 * authenticated blob URLs.
 */
export async function resolveChatMediaUrl(src: string): Promise<string> {
  const raw = (src || '').trim().replace(/^<|>$/g, '')
  if (!raw) return ''
  if (/^\/\//.test(raw)) return ''

  // data: / blob: pass through (already displayable)
  if (/^(data:|blob:)/i.test(raw)) {
    if (/^data:(?!image\/)/i.test(raw)) return ''
    return raw
  }

  // Loopback API attachment / media URLs need Bearer → blob
  if (isAuthenticatedApiUrl(raw)) {
    let absolute = raw
    if (raw.startsWith('/api/')) {
      // Prefer same API base the app already uses
      const base = getApiBase().replace(/\/api\/?$/, '')
      absolute = `${base || getServerUrl()}${raw}`
    }
    try {
      return await fetchAuthedBlob(absolute, `api:${absolute}`)
    } catch (first) {
      // Fallback: session attachment URL → /api/media under ~/.remedy/attachments
      const m = absolute.match(
        /\/api\/sessions\/([^/]+)\/attachments\/([^/?#]+)/i,
      )
      if (m) {
        const sid = m[1]!
        const name = decodeURIComponent(m[2]!)
        const relative = `attachments/${sid}/${name}`
        try {
          const url = `${getApiBase()}/media?path=${encodeURIComponent(relative)}`
          return await fetchAuthedBlob(url, `media-rel:${relative}`)
        } catch {
          // Bare filename search (server walks attachments/**)
          try {
            const url = `${getApiBase()}/media?path=${encodeURIComponent(name)}`
            return await fetchAuthedBlob(url, `media-name:${name}`)
          } catch {
            /* fall through */
          }
        }
      }
      throw first
    }
  }

  // Public https only (not loopback)
  if (/^https?:\/\//i.test(raw)) {
    if (/^(javascript|vbscript):/i.test(raw)) return ''
    try {
      const u = new URL(raw)
      const host = u.hostname.toLowerCase()
      if (host === '127.0.0.1' || host === 'localhost' || host === '::1') {
        // Non-/api loopback — still try authed fetch (rare)
        return fetchAuthedBlob(raw, `http:${raw}`)
      }
    } catch {
      return ''
    }
    return raw
  }

  if (!isLocalMediaPath(raw)) return ''

  const path = normalizeLocalMediaPath(raw)
  if (!path) return ''

  const hit = cacheGet(path)
  if (hit) return hit

  await ensureApiToken()
  const url = `${getApiBase()}/media?path=${encodeURIComponent(path)}`
  return fetchAuthedBlob(url, path)
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
