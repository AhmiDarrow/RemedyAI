declare global {
  interface Window {
    __TAURI__?: unknown
    __TAURI_INTERNALS__?: unknown
  }
}

const SERVER_URL = 'http://127.0.0.1:7400'

let _apiToken: string | null = null
let _tokenPromise: Promise<string | null> | null = null

function inTauriShell(): boolean {
  if (typeof window === 'undefined') return false
  return !!(window.__TAURI__ || window.__TAURI_INTERNALS__)
}

function getApiBase(): string {
  // Desktop shell always talks to the local sidecar (not Vite's relative /api).
  if (inTauriShell()) {
    return `${SERVER_URL}/api`
  }
  return '/api'
}

/** Load local API bearer token (once). Loopback bootstrap or Tauri file read. */
export async function ensureApiToken(): Promise<string | null> {
  if (_apiToken) return _apiToken
  if (_tokenPromise) return _tokenPromise
  _tokenPromise = (async () => {
    try {
      // Prefer loopback bootstrap (works for desktop + vite proxy to 127.0.0.1)
      const r = await fetch(`${SERVER_URL}/api/auth/local-bootstrap`, {
        headers: { Accept: 'application/json' },
      })
      if (r.ok) {
        const data = (await r.json()) as { token?: string }
        if (data.token) {
          _apiToken = data.token
          return _apiToken
        }
      }
    } catch {
      /* server may still be starting */
    }
    try {
      if (inTauriShell()) {
        const { invoke } = await import('@tauri-apps/api/core')
        const t = await invoke<string>('get_local_api_token')
        if (t) {
          _apiToken = t
          return _apiToken
        }
      }
    } catch {
      /* command may be missing on older builds */
    }
    return null
  })()
  return _tokenPromise
}

export function authHeaders(): Record<string, string> {
  if (!_apiToken) return {}
  return {
    Authorization: `Bearer ${_apiToken}`,
    'X-Remedy-Token': _apiToken,
  }
}

interface FetchOptions extends RequestInit {
  timeout?: number
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export { getApiBase, SERVER_URL }

export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { timeout = 30000, ...fetchOpts } = options

  await ensureApiToken()

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  try {
    const res = await fetch(`${getApiBase()}${path}`, {
      ...fetchOpts,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
        ...fetchOpts.headers,
      },
    })

    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(res.status, body?.error || body?.detail || res.statusText)
    }

    return (await res.json()) as T
  } finally {
    clearTimeout(timeoutId)
  }
}

export async function healthCheck(timeout = 2000): Promise<boolean> {
  try {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), timeout)
    const res = await fetch(`${SERVER_URL}/api/status`, { signal: controller.signal })
    clearTimeout(timeoutId)
    return res.ok
  } catch {
    return false
  }
}
