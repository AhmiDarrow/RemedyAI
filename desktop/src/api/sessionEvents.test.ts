import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  authHeaders: () => ({}),
  clearApiToken: () => {},
  ensureApiToken: async () => 'tok',
  getServerUrl: () => 'http://127.0.0.1:7400',
}))

import { subscribeSessionEvents } from './sessionEvents'

function emptyStream(): Response {
  return new Response(new ReadableStream({ start: (c) => c.close() }), { status: 200 })
}

function streamOf(text: string): Response {
  const bytes = new TextEncoder().encode(text)
  return new Response(
    new ReadableStream({
      start: (c) => {
        c.enqueue(bytes)
        c.close()
      },
    }),
    { status: 200 },
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('session events stream', () => {
  it('waits before reconnecting after a clean close', async () => {
    const fetchMock = vi.fn(async () => emptyStream())
    vi.stubGlobal('fetch', fetchMock)
    const stop = subscribeSessionEvents({})
    await new Promise((r) => setTimeout(r, 200))
    stop()
    // A server that answers 200 and closes at once used to be hit in a loop.
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(2)
  })

  it('dispatches CRLF-framed events', async () => {
    const seen: string[] = []
    vi.stubGlobal('fetch', vi.fn(async () => streamOf('data: {"type":"session_created","session_id":"s1"}\r\n\r\n')))
    const stop = subscribeSessionEvents({ onEvent: (ev) => seen.push(ev.type) })
    await new Promise((r) => setTimeout(r, 100))
    stop()
    expect(seen).toContain('session_created')
  })
})
