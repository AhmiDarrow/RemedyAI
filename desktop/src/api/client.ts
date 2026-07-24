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

/** Clear cached token so the next call re-bootstraps (e.g. after server restart). */
export function clearApiToken(): void {
  _apiToken = null
  _tokenPromise = null
}

/** Load local API bearer token (retries when previous attempt failed). */
export async function ensureApiToken(): Promise<string | null> {
  if (_apiToken) return _apiToken
  if (_tokenPromise) return _tokenPromise

  _tokenPromise = (async () => {
    // Prefer OS/desktop IPC (no HTTP bootstrap) when running inside Tauri.
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
      /* command may be missing on older builds — fall through to HTTP */
    }
    // Browser WebUI / dev: loopback-only bootstrap (same Windows user boundary).
    // Prefer same-origin when the SPA is served by the local API (avoids
    // localhost vs 127.0.0.1 cross-origin "Failed to fetch" surprises).
    try {
      const bootstrapUrls: string[] = []
      if (typeof window !== 'undefined') {
        const origin = window.location.origin || ''
        if (
          origin.includes('127.0.0.1:7400')
          || origin.includes('localhost:7400')
        ) {
          bootstrapUrls.push(`${origin}/api/auth/local-bootstrap`)
        }
      }
      bootstrapUrls.push(`${SERVER_URL}/api/auth/local-bootstrap`)
      for (const url of bootstrapUrls) {
        try {
          const r = await fetch(url, {
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
          /* try next URL */
        }
      }
    } catch {
      /* server may still be starting */
    }
    // Allow retry on next call (do not cache permanent failure)
    _tokenPromise = null
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

/** Flatten FastAPI / gateway error bodies into a short user-facing string. */
export function formatApiErrorBody(body: unknown, fallback = 'Request failed'): string {
  if (body == null || body === '') return fallback
  if (typeof body === 'string') return body
  if (typeof body !== 'object') return String(body)
  const o = body as Record<string, unknown>
  const detail = o.detail ?? o.error ?? o.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const row = item as Record<string, unknown>
          const loc = Array.isArray(row.loc) ? row.loc.join('.') : ''
          const msg = typeof row.msg === 'string' ? row.msg : JSON.stringify(row)
          return loc ? `${loc}: ${msg}` : msg
        }
        return String(item)
      })
      .filter(Boolean)
    if (parts.length) return parts.join('; ')
  }
  if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail)
    } catch {
      /* fall through */
    }
  }
  if (typeof o.error === 'string' && o.error.trim()) return o.error
  try {
    return JSON.stringify(o)
  } catch {
    return fallback
  }
}

export { getApiBase, SERVER_URL }

/** Wait until /api/status answers (sidecar still booting on fresh install). */
export async function waitForLocalApi(maxMs = 15000): Promise<boolean> {
  const started = Date.now()
  let delay = 200
  while (Date.now() - started < maxMs) {
    if (await healthCheck(1500)) return true
    await new Promise((r) => setTimeout(r, delay))
    delay = Math.min(delay * 1.5, 1500)
  }
  return healthCheck(1500)
}

export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { timeout = 30000, ...fetchOpts } = options

  // Retry token bootstrap a few times — first-run can race the sidecar.
  let token = await ensureApiToken()
  if (!token) {
    for (let i = 0; i < 4 && !token; i++) {
      await new Promise((r) => setTimeout(r, 200 * (i + 1)))
      clearApiToken()
      token = await ensureApiToken()
    }
  }

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  try {
    let res: Response
    try {
      res = await fetch(`${getApiBase()}${path}`, {
        ...fetchOpts,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
          ...fetchOpts.headers,
        },
      })
    } catch (e: unknown) {
      const name = e instanceof Error ? e.name : ''
      if (name === 'AbortError') {
        throw new ApiError(0, `Request timed out after ${timeout}ms (${path})`)
      }
      const msg = e instanceof Error ? e.message : String(e)
      // "Failed to fetch" is also what Chromium reports for CORS preflight failures
      // (auth middleware blocking OPTIONS used to look like a dead server).
      const unreachable =
        msg.includes('Failed to fetch')
        || msg.includes('NetworkError')
        || msg.includes('Load failed')
      throw new ApiError(
        0,
        unreachable
          ? `Cannot reach local API at ${SERVER_URL} (${path}). `
            + 'Is the server running? If setup just opened, wait a second and retry. '
            + 'Use Retry on the splash if the local server failed to start.'
          : msg || `Network error (${path})`,
      )
    }

    // One retry after re-bootstrap on 401 (token rotated after wipe/reinstall)
    if (res.status === 401) {
      clearApiToken()
      await ensureApiToken()
      try {
        res = await fetch(`${getApiBase()}${path}`, {
          ...fetchOpts,
          signal: controller.signal,
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders(),
            ...fetchOpts.headers,
          },
        })
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        throw new ApiError(
          0,
          msg.includes('Failed to fetch')
            ? `Cannot reach local API at ${SERVER_URL} (${path}). Is the server running?`
            : msg || `Network error (${path})`,
        )
      }
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new ApiError(
        res.status,
        formatApiErrorBody(body, res.statusText || `HTTP ${res.status}`),
      )
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
