import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiFetch, authHeaders, clearApiToken, ensureApiToken, formatApiErrorBody } from './client'

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
