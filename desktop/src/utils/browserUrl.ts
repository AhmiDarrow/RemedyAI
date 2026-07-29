/** Default homepage for the in-app Browser slide (override in Settings). */
export const DEFAULT_BROWSER_HOME = 'https://github.com/AhmiDarrow/RemedyAI'

/**
 * Normalize a user-entered browser URL for iframe / external open.
 * Only http(s) and about: are allowed through as-is; bare hosts get https://.
 * Rejects task-text leaks (spaces, emails, "gmail sign in…").
 */
export function normalizeBrowserUrl(raw: string): string {
  let u = (raw || '').trim()
  if (!u) return ''
  // Block dangerous schemes that must never land in iframe src / shell open.
  if (/^(javascript|data|vbscript|file):/i.test(u)) return ''
  // Task prose must never become the address bar
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
