/** Default homepage for the in-app Browser slide (override in Settings). */
export const DEFAULT_BROWSER_HOME = 'https://github.com/AhmiDarrow/RemedyAI'

/**
 * Default search engine for omnibox (privacy-friendly; no API key).
 * Query is URL-encoded into `q=`.
 */
export const DEFAULT_BROWSER_SEARCH =
  'https://duckduckgo.com/?q={query}'

/**
 * Normalize a user-entered browser URL for iframe / external open.
 * Only http(s) and about: are allowed through as-is; bare hosts get https://.
 * Rejects prose / non-URL text (use {@link resolveBrowserAddressBar} for search).
 */
export function normalizeBrowserUrl(raw: string): string {
  let u = (raw || '').trim()
  if (!u) return ''
  // Block dangerous schemes that must never land in iframe src / shell open.
  if (/^(javascript|data|vbscript|file):/i.test(u)) return ''
  // Spaces / bare emails are not URLs (search path handles those)
  if (/\s/.test(u) || (u.includes('@') && !/^https?:\/\//i.test(u))) return ''
  const hostPart = (u.split(/[?#]/)[0] || u).replace(/^https?:\/\//i, '')
  if (/[,;"']/.test(hostPart)) return ''
  if (!/^https?:\/\//i.test(u) && !u.startsWith('about:')) {
    // bare host only
    if (
      !/^(?:www\.)?[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}(?:\/.*)?$/i.test(u) &&
      u !== 'localhost' &&
      !/^localhost:\d+/.test(u)
    ) {
      return ''
    }
    u = `https://${u}`
  }
  if (u.startsWith('about:')) return u
  try {
    const parsed = new URL(u)
    if (!parsed.hostname || /\s/.test(parsed.hostname)) return ''
  } catch {
    return ''
  }
  return u
}

/**
 * Build a search-engine URL for free text (omnibox).
 * `template` uses `{query}` placeholder; defaults to DuckDuckGo.
 */
export function browserSearchUrl(
  query: string,
  template: string = DEFAULT_BROWSER_SEARCH,
): string {
  const q = (query || '').trim()
  if (!q) return ''
  if (/^(javascript|data|vbscript|file):/i.test(q)) return ''
  const tpl = (template || DEFAULT_BROWSER_SEARCH).includes('{query}')
    ? template || DEFAULT_BROWSER_SEARCH
    : DEFAULT_BROWSER_SEARCH
  return tpl.replace(/\{query\}/g, encodeURIComponent(q))
}

/**
 * Address-bar resolve: real URL if possible, otherwise web search.
 * Use this for Go / Enter in the Browser slide.
 */
export function resolveBrowserAddressBar(
  raw: string,
  searchTemplate?: string | null,
): string {
  const asUrl = normalizeBrowserUrl(raw)
  if (asUrl) return asUrl
  const q = (raw || '').trim()
  if (!q) return ''
  return browserSearchUrl(q, searchTemplate || DEFAULT_BROWSER_SEARCH)
}

/** Settings / home button: normalize or fall back to Remedy GitHub. */
export function resolveBrowserHome(raw?: string | null): string {
  return normalizeBrowserUrl(raw || '') || DEFAULT_BROWSER_HOME
}

/** True when the URL is safe to open externally (matches Rust open_external_url). */
export function isOpenableBrowserUrl(url: string): boolean {
  const u = (url || '').trim()
  return (
    (u.startsWith('http://') || u.startsWith('https://') || u.startsWith('about:')) &&
    normalizeBrowserUrl(u) === u
  )
}
