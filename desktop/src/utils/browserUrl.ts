/**
 * Normalize a user-entered browser URL for iframe / external open.
 * Only http(s) and about: are allowed through as-is; bare hosts get https://.
 */
export function normalizeBrowserUrl(raw: string): string {
  let u = (raw || '').trim()
  if (!u) return ''
  // Block dangerous schemes that must never land in iframe src / shell open.
  if (/^(javascript|data|vbscript|file):/i.test(u)) return ''
  if (!/^https?:\/\//i.test(u) && !u.startsWith('about:')) {
    u = `https://${u}`
  }
  return u
}

/** True when the URL is safe to open externally (matches Rust open_external_url). */
export function isOpenableBrowserUrl(url: string): boolean {
  const u = (url || '').trim()
  return (
    u.startsWith('http://') ||
    u.startsWith('https://') ||
    u.startsWith('about:')
  )
}
