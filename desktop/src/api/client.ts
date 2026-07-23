declare global {
  interface Window {
    __TAURI__?: unknown
    __TAURI_INTERNALS__?: unknown
  }
}

const SERVER_URL = 'http://127.0.0.1:7400'

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

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  try {
    const res = await fetch(`${getApiBase()}${path}`, {
      ...fetchOpts,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
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
