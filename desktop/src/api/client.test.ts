import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, authHeaders, clearApiToken, ensureApiToken, formatApiErrorBody, pickServerUrl } from './client'

afterEach(() => { clearApiToken(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('browser authentication', () => {
  it.each(['http://localhost:5177', 'http://127.0.0.1:7417', 'http://[::1]:5177'])(
    'bootstraps through the same origin at %s', async (origin) => {
      vi.stubGlobal('window', { location: { origin } })
      const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ token: 'not-a-real-test-token' }) })
      vi.stubGlobal('fetch', fetcher)
      expect(await ensureApiToken()).toBe('not-a-real-test-token')
      expect(fetcher.mock.calls[0][0]).toBe(`${origin}/api/auth/local-bootstrap`)
    },
  )

  it('uses a one-time ?bootstrap= link once, strips it, and keeps the tab token', async () => {
    const replaceState = vi.fn()
    const store = new Map<string, string>()
    const sessionStorage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v) },
      removeItem: (k: string) => { store.delete(k) },
    }
    vi.stubGlobal('window', {
      location: { origin: 'http://127.0.0.1:7400', href: 'http://127.0.0.1:7400/chat?bootstrap=one-time-nonce&x=1#h' },
      history: { state: null, replaceState },
      sessionStorage,
    })
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ token: 'not-a-real-test-token' }) })
    vi.stubGlobal('fetch', fetcher)
    expect(await ensureApiToken()).toBe('not-a-real-test-token')
    expect(fetcher.mock.calls[0][0]).toBe('http://127.0.0.1:7400/api/auth/local-bootstrap?nonce=one-time-nonce')
    expect(replaceState).toHaveBeenCalledWith(null, '', '/chat?x=1#h')
    expect(store.get('remedy.linkToken')).toBe('not-a-real-test-token')

    // Reload in the same tab: the spent link is not resent; the tab copy is used.
    clearApiToken()
    store.set('remedy.linkToken', 'not-a-real-test-token')
    vi.stubGlobal('window', {
      location: { origin: 'http://127.0.0.1:7400', href: 'http://127.0.0.1:7400/chat' },
      history: { state: null, replaceState },
      sessionStorage,
    })
    fetcher.mockClear()
    expect(await ensureApiToken()).toBe('not-a-real-test-token')
    expect(fetcher).not.toHaveBeenCalled()

    // A rejected token (clearApiToken) drops the tab copy.
    clearApiToken()
    expect(store.has('remedy.linkToken')).toBe(false)
  })

  it('does not send bootstrap to an origin that only contains localhost', async () => {
    vi.stubGlobal('window', { location: { origin: 'https://localhost:5177.attacker.invalid' } })
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ token: 'not-a-real-test-token' }) })
    vi.stubGlobal('fetch', fetcher)
    await ensureApiToken()
    expect(fetcher.mock.calls[0][0]).not.toContain('attacker.invalid')
  })
})

describe('formatApiErrorBody', () => {
  it('prefers string detail', () => {
    expect(formatApiErrorBody({ detail: 'boom' })).toBe('boom')
  })

  it('flattens FastAPI validation arrays', () => {
    const msg = formatApiErrorBody({
      detail: [{ loc: ['body', 'llm_provider'], msg: 'field required' }],
    })
    expect(msg).toContain('llm_provider')
    expect(msg).toContain('field required')
  })

  it('falls back for empty body', () => {
    expect(formatApiErrorBody(null, 'fallback')).toBe('fallback')
  })

  it('falls back for empty object/array JSON bodies', () => {
    expect(formatApiErrorBody({}, 'Service Unavailable')).toBe('Service Unavailable')
    expect(formatApiErrorBody([], 'HTTP 500')).toBe('HTTP 500')
  })
})

describe('API request lifecycle', () => {
  const tokenResponse = () => ({ ok: true, json: async () => ({ token: 'not-a-real-test-token' }) })
  const pendingFetch = (_url: string, options: RequestInit) => new Promise<Response>((_resolve, reject) => {
    const aborted = () => reject(options.signal?.reason)
    if (options.signal?.aborted) aborted()
    else options.signal?.addEventListener('abort', aborted, { once: true })
  })

  it('does not bootstrap or send an already cancelled request', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    const controller = new AbortController()
    controller.abort()
    await expect(apiFetch('/settings', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('includes authentication in the request deadline', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(pendingFetch))
    const result = expect(apiFetch('/settings', { timeout: 50 })).rejects.toThrow('Request timed out after 50ms')
    await vi.advanceTimersByTimeAsync(50)
    await result
    await vi.advanceTimersByTimeAsync(6000)
  })

  it('cancels one waiter without poisoning shared authentication', async () => {
    let finish!: (value: ReturnType<typeof tokenResponse>) => void
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({ saved: true }) })
    vi.stubGlobal('fetch', fetcher)
    const controller = new AbortController()
    const cancelled = expect(apiFetch('/one', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    const survivor = apiFetch('/two')
    controller.abort()
    finish(tokenResponse())
    await cancelled
    await expect(survivor).resolves.toEqual({ saved: true })
    expect(fetcher.mock.calls.map(call => call[0])).not.toContain('/api/one')
  })

  it('propagates cancellation to an in-flight fetch', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(tokenResponse()).mockImplementation(pendingFetch))
    await ensureApiToken()
    const controller = new AbortController()
    const result = expect(apiFetch('/settings', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    await Promise.resolve()
    controller.abort()
    await result
  })

  it('preserves Headers objects and accepts an empty successful response', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(tokenResponse()).mockResolvedValue({ ok: true, status: 204 })
    vi.stubGlobal('fetch', fetcher)
    await expect(apiFetch('/settings', { headers: new Headers({ 'X-Example': 'value' }) })).resolves.toBeUndefined()
    const headers = fetcher.mock.calls[1][1].headers as Headers
    expect(headers.get('X-Example')).toBe('value')
    expect(headers.get('Authorization')).toBe('Bearer not-a-real-test-token')
  })

  it('does not restore a stale token after authentication was cleared', async () => {
    let finish!: (value: ReturnType<typeof tokenResponse>) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { finish = resolve })))
    const pending = ensureApiToken()
    clearApiToken()
    finish(tokenResponse())
    await expect(pending).resolves.toBeNull()
    expect(authHeaders()).toEqual({})
  })

  it.each(['TypeError: network error', 'Failed to fetch', 'Load failed'])(
    'explains the network failure family: %s', async (failure) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(tokenResponse()).mockRejectedValue(new Error(failure)))
      await expect(apiFetch('/settings')).rejects.toThrow('Cannot reach local API')
    },
  )
})

describe('pickServerUrl', () => {
  const base = { prod: true, desktop: false }
  it('a built WebUI talks to the runtime that served it, on any port', () => {
    expect(pickServerUrl({ ...base, origin: 'http://127.0.0.1:7410' })).toBe('http://127.0.0.1:7410')
    expect(pickServerUrl({ ...base, origin: 'http://localhost:7400' })).toBe('http://localhost:7400')
    expect(pickServerUrl({ ...base, origin: 'http://[::1]:7420' })).toBe('http://[::1]:7420')
  })
  it('keeps the default for desktop, the dev server, and non-loopback pages', () => {
    expect(pickServerUrl({ ...base, desktop: true, origin: 'http://tauri.localhost' })).toBe('http://127.0.0.1:7400')
    expect(pickServerUrl({ prod: false, desktop: false, origin: 'http://localhost:5173' })).toBe('http://127.0.0.1:7400')
    expect(pickServerUrl({ ...base, origin: 'https://example.com' })).toBe('http://127.0.0.1:7400')
    expect(pickServerUrl({ ...base, origin: 'http://127.0.0.1.evil.com:7410' })).toBe('http://127.0.0.1:7400')
    expect(pickServerUrl({ ...base, origin: '' })).toBe('http://127.0.0.1:7400')
  })
  it('explicit env and injected origins still win', () => {
    expect(pickServerUrl({ ...base, envUrl: 'http://127.0.0.1:7410/api/', origin: 'http://127.0.0.1:7999' })).toBe(
      'http://127.0.0.1:7410',
    )
    expect(pickServerUrl({ ...base, injected: 'http://127.0.0.1:7411', origin: 'http://127.0.0.1:7999' })).toBe(
      'http://127.0.0.1:7411',
    )
  })
})
