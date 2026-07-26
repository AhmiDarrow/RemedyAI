/**
 * Resolve markdown image `src` so chat can display local files regardless of
 * LLM provider. Models often emit `assets/foo.png` or `C:\\...\\bar.png` —
 * WebView cannot load those as bare paths.
 */
import { ensureApiToken, getApiBase, authHeaders } from '../api/client'

const blobCache = new Map<string, string>()

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
  if (isRemoteOrDataUrl(raw)) return raw
  if (!isLocalMediaPath(raw)) return raw

  const path = normalizeLocalMediaPath(raw)
  const cached = blobCache.get(path)
  if (cached) return cached

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
  blobCache.set(path, objectUrl)
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
