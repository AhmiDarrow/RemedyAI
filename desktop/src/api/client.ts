declare global {
  interface Window {
    __TAURI__?: unknown
    __TAURI_INTERNALS__?: unknown
    /** Optional inject for dual-instance / tests */
    __REMEDY_API_ORIGIN__?: string
  }
}

/**
 * Local API origin (no trailing slash, no `/api` suffix).
 * - `VITE_REMEDY_API` override
 * - `window.__REMEDY_API_ORIGIN__` optional inject
 * - default release: `http://127.0.0.1:7400`
 */
function resolveServerUrl(): string {
  try {
    const envUrl = (import.meta as ImportMeta & { env?: Record<string, string> }).env
      ?.VITE_REMEDY_API
    if (envUrl && String(envUrl).trim()) {
      return String(envUrl)
        .trim()
        .replace(/\/$/, '')
        .replace(/\/api$/i, '')
    }
  } catch {
    /* non-vite */
  }
  if (typeof window !== 'undefined' && window.__REMEDY_API_ORIGIN__) {
    return String(window.__REMEDY_API_ORIGIN__)
      .trim()
      .replace(/\/$/, '')
      .replace(/\/api$/i, '')
  }
  return 'http://127.0.0.1:7400'
}

/** Origin of the local API (e.g. http://127.0.0.1:7400 or :7410 for isolated dev). */
export function getServerUrl(): string {
  return resolveServerUrl()
}

let _tokenGeneration = 0
let _apiToken: string | null = null
let _tokenPromise: Promise<string | null> | null = null

function inTauriShell(): boolean {
  if (typeof window === 'undefined') return false
  return !!(window.__TAURI__ || window.__TAURI_INTERNALS__)
}

function getApiBase(): string {
  // Desktop shell always talks to the local sidecar (not Vite's relative /api).
  if (inTauriShell()) {
    return `${getServerUrl()}/api`
  }
  return '/api'
}
/** Clear cached token so the next call re-bootstraps (e.g. after server restart). */
export function clearApiToken(): void {
  _tokenGeneration += 1
  _apiToken = null
  _tokenPromise = null
}

/** Load local API bearer token (retries when previous attempt failed). */
export async function ensureApiToken(): Promise<string | null> {
  if (_apiToken) return _apiToken
  if (_tokenPromise) return _tokenPromise

  const generation = _tokenGeneration
  _tokenPromise = (async () => {
    // Prefer OS/desktop IPC (no HTTP bootstrap) when running inside Tauri.
    try {
      if (inTauriShell()) {
        const { invoke } = await import('@tauri-apps/api/core')
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), 3000)
        let t: string
        try {
          t = await waitWithAbort(invoke<string>('get_local_api_token'), controller.signal)
        } finally {
          clearTimeout(timer)
        }
        // Reject sealed DPAPI envelopes if an older host returned the raw file
        // instead of decrypting — fall through to loopback bootstrap.
        const trimmed = (t || '').trim()
        const looksSealed =
          trimmed.startsWith('{')
          && (trimmed.includes('"dpapi"') || trimmed.includes('"encoding"'))
        if (generation !== _tokenGeneration) return null
        if (trimmed.length >= 16 && !looksSealed) {
          _apiToken = trimmed
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
        // Same-origin when SPA is served by the local API (any port).
        if (/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i.test(origin)) {
          // Vite proxies /api too. Same-origin bootstrap works at every dev
          // port without requiring extra CORS origins on the runtime.
          bootstrapUrls.push(`${origin}/api/auth/local-bootstrap`)
        }
      }
      bootstrapUrls.push(`${getServerUrl()}/api/auth/local-bootstrap`)
      for (const url of new Set(bootstrapUrls)) {
        const controller = new AbortController()
        const timer = setTimeout(() => controller.abort(), 3000)
        try {
          const r = await fetch(url, {
            signal: controller.signal,
            headers: { Accept: 'application/json' },
          })
          if (r.ok) {
            const data = (await r.json()) as { token?: string }
            if (generation !== _tokenGeneration) return null
            if (typeof data.token === 'string' && data.token.trim().length >= 16) {
              _apiToken = data.token.trim()
              return _apiToken
            }
          }
        } catch {
          /* try next URL */
        } finally {
          clearTimeout(timer)
        }
      }
    } catch {
      /* server may still be starting */
    }
    // Allow retry on next call (do not cache permanent failure)
    if (generation === _tokenGeneration) _tokenPromise = null
    return null
  })()

  const pending = _tokenPromise
  const settled = () => { if (_tokenPromise === pending) _tokenPromise = null }
  void pending.then(settled, settled)
  return pending
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
    const s = JSON.stringify(o)
    // Empty {} / [] from res.json().catch(() => ({})) — use status text instead.
    if (s === '{}' || s === '[]') return fallback
    return s
  } catch {
    return fallback
  }
}

export { getApiBase }

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

/** Cancel waiting for shared work without cancelling another request's work. */
function waitWithAbort<T>(work: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const aborted = () => reject(signal.reason ?? new DOMException('Request cancelled', 'AbortError'))
    if (signal.aborted) { aborted(); return }
    signal.addEventListener('abort', aborted, { once: true })
    work.then(resolve, reject).finally(() => signal.removeEventListener('abort', aborted))
  })
}

export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { timeout = 30000, signal: callerSignal, ...fetchOpts } = options
  const controller = new AbortController()
  const abort = () => controller.abort(callerSignal?.reason)
  if (callerSignal?.aborted) abort()
  else callerSignal?.addEventListener('abort', abort, { once: true })
  const timeoutId = setTimeout(() => controller.abort(), timeout)
  const wait = <V,>(work: Promise<V>) => waitWithAbort(work, controller.signal)
  const request = () => {
    controller.signal.throwIfAborted()
    const headers = new Headers({ 'Content-Type': 'application/json', ...authHeaders() })
    new Headers(fetchOpts.headers).forEach((value, key) => headers.set(key, value))
    return fetch(`${getApiBase()}${path}`, { ...fetchOpts, signal: controller.signal, headers })
  }
  try {
    controller.signal.throwIfAborted()
    // The overall deadline includes bootstrap and retries, not just fetch.
    let token = await wait(ensureApiToken())
    for (let i = 0; i < 4 && !token; i++) {
      await wait(new Promise((resolve) => setTimeout(resolve, 200 * (i + 1))))
      controller.signal.throwIfAborted()
      token = await wait(ensureApiToken())
    }
    let res = await request()
    if (res.status === 401) {
      clearApiToken()
      await wait(ensureApiToken())
      res = await request()
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      controller.signal.throwIfAborted()
      throw new ApiError(res.status, formatApiErrorBody(body, res.statusText || `HTTP ${res.status}`))
    }
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  } catch (error: unknown) {
    if (callerSignal?.aborted) throw callerSignal.reason ?? new DOMException('Request cancelled', 'AbortError')
    if (controller.signal.aborted) throw new ApiError(0, `Request timed out after ${timeout}ms (${path})`)
    if (error instanceof ApiError) throw error
    const message = error instanceof Error ? error.message : String(error)
    const unreachable = /failed to fetch|network\s?error|load failed/i.test(message)
    throw new ApiError(0, unreachable
      ? `Cannot reach local API at ${getServerUrl()} (${path}). Check the connection and try again.`
      : message || `Network error (${path})`)
  } finally {
    clearTimeout(timeoutId)
    callerSignal?.removeEventListener('abort', abort)
  }
}

/**
 * Liveness probe for splash + status bar.
 * Prefers ultra-light `/api/ping` (no DB); falls back to `/api/status`.
 */
export async function healthCheck(timeout = 2000): Promise<boolean> {
  // Always re-resolve origin (multi-port / late inject).
  const origin = getServerUrl()
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)
  try {
    // Ping first — must stay responsive even if heavier handlers are busy.
    try {
      const ping = await fetch(`${origin}/api/ping`, {
        signal: controller.signal,
        headers: { Accept: 'application/json' },
      })
      if (ping.ok) return true
    } catch {
      /* fall through to /api/status for older sidecars */
    }
    if (controller.signal.aborted) return false
    const res = await fetch(`${origin}/api/status`, {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    })
    return res.ok
  } catch {
    return false
  } finally {
    clearTimeout(timeoutId)
  }
}

/** Lightweight client-side debug trail (console + optional ring for future UI). */
const _debugRing: string[] = []
const _DEBUG_RING_MAX = 200

export function clientDebug(scope: string, message: string, extra?: unknown): void {
  const ts = new Date().toISOString()
  const line =
    extra !== undefined
      ? `[${ts}] [${scope}] ${message} ${typeof extra === 'string' ? extra : JSON.stringify(extra)}`
      : `[${ts}] [${scope}] ${message}`
  _debugRing.push(line)
  if (_debugRing.length > _DEBUG_RING_MAX) _debugRing.shift()
  // Always log — desktop users diagnose from DevTools / tauri log plugin.
  if (extra !== undefined) {
    console.debug(`[remedy:${scope}]`, message, extra)
  } else {
    console.debug(`[remedy:${scope}]`, message)
  }
}

export function getClientDebugRing(): string[] {
  return [..._debugRing]
}
