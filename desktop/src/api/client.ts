const API_BASE = '/api'

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

export async function apiFetch<T = unknown>(
  path: string,
  options: FetchOptions = {},
): Promise<T> {
  const { timeout = 30000, ...fetchOpts } = options

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  try {
    const res = await fetch(`${API_BASE}${path}`, {
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
