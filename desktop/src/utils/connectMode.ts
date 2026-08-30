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

/** True for IPv4 loopback. A phone cannot reach this bind. */
export function isConnectLoopbackHost(host: string): boolean {
  const h = (host || '').trim()
  return h === '127.0.0.1' || h.startsWith('127.')
}

export const CONNECT_LOOPBACK_BIND_WARNING =
  '127.0.0.1 is only reachable on this computer. Pick a LAN address so the phone can connect.'

/**
 * Default Connect bind: keep an explicit current host (even loopback);
 * otherwise first non-loopback unicast, else the only remaining address.
 */
export function preferredConnectBindHost(
  addrs: readonly string[],
  current?: string | null,
): string {
  const cur = (current || '').trim()
  if (cur) return cur
  const rows = addrs.map((ip) => (ip || '').trim()).filter(Boolean)
  const lan = rows.find((ip) => !isConnectLoopbackHost(ip))
  return lan || rows[0] || ''
}
