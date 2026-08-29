/** Phone compact chrome: same SPA with ?connect=1. */

export function isConnectCompact(
  search: string = typeof window !== 'undefined' ? window.location.search : '',
): boolean {
  const q = search.startsWith('?') ? search.slice(1) : search
  try {
    return new URLSearchParams(q).get('connect') === '1'
  } catch {
    return false
  }
}
