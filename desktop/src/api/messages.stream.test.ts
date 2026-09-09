import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  apiFetch: vi.fn(),
  authHeaders: () => ({}),
  clearApiToken: () => {},
  ensureApiToken: async () => {},
  formatApiErrorBody: (_body: unknown, fallback: string) => fallback,
  getApiBase: () => 'http://127.0.0.1:7400/api',
}))
vi.mock('../sessions/focusedSession', () => ({
  sessionSelectHeaders: () => ({}),
  shouldRetryStreamAfter409: () => false,
}))

import { streamMessage, type StreamDonePayload, type UsagePayload } from './messages'

/** Serve `chunks` as an SSE body, then close the connection. */
function sseResponse(chunks: string[]): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c))
      controller.close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

type Run = {
  tokens: string[]
  done: StreamDonePayload[]
  errors: string[]
  usage: UsagePayload[]
  calls: { name: string; callId?: string }[]
  results: { name: string; callId?: string; ok?: boolean }[]
  finished: Promise<void>
}

function run(chunks: string[]): Run {
  const out: Omit<Run, 'finished'> = {
    tokens: [],
    done: [],
    errors: [],
    usage: [],
    calls: [],
    results: [],
  }
  vi.mocked(globalThis.fetch).mockResolvedValueOnce(sseResponse(chunks))
  const finished = new Promise<void>((resolve) => {
    streamMessage('s1', 'hello', {
      onToken: (t) => out.tokens.push(t),
      onDone: (d) => {
        out.done.push(d)
        resolve()
      },
      onError: (e) => {
        out.errors.push(e)
        resolve()
      },
      onToolCall: (name, _args, callId) => out.calls.push({ name, callId }),
      onToolResult: (name, _preview, ok, callId) => out.results.push({ name, callId, ok }),
      onUsage: (u) => out.usage.push(u),
    })
  })
  return { ...out, finished }
}

describe('streamMessage close handling', () => {
  const origFetch = globalThis.fetch

  beforeEach(() => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = origFetch
    vi.restoreAllMocks()
  })

  it('reports a close without a terminal frame as interrupted, keeping tokens', async () => {
    const r = run(['event: token\ndata: {"text":"partial "}\n\n', 'event: token\ndata: {"text":"answer"}\n\n'])
    await r.finished
    expect(r.tokens.join('')).toBe('partial answer')
    expect(r.errors).toEqual([])
    expect(r.done).toHaveLength(1)
    expect(r.done[0]).toMatchObject({ request_id: '', interrupted: true })
  })

  it('never reports success with an empty request id', async () => {
    const r = run(['event: token\ndata: {"text":"x"}\n\n'])
    await r.finished
    for (const d of r.done) {
      if (!d.request_id) {
        expect(d.interrupted || d.aborted || d.steered).toBe(true)
      }
    }
  })

  it('a done frame completes normally even when the socket closes right after', async () => {
    const r = run(['event: token\ndata: {"text":"ok"}\n\nevent: done\ndata: {"request_id":"req-1"}\n\n'])
    await r.finished
    expect(r.done).toEqual([{ request_id: 'req-1' }])
  })

  it('handles a terminal frame split across reads with CRLF', async () => {
    const r = run(['event: don', 'e\r\ndata: {"request_', 'id":"req-2"}\r\n\r\n'])
    await r.finished
    expect(r.done).toEqual([{ request_id: 'req-2' }])
  })

  it('derives total_tokens for Go-style usage frames without an event: line', async () => {
    const r = run([
      'data: {"type":"usage","prompt_tokens":100,"completion_tokens":25}\n\n',
      'event: done\ndata: {"request_id":"req-3"}\n\n',
    ])
    await r.finished
    expect(r.usage[0]).toMatchObject({
      prompt_tokens: 100,
      completion_tokens: 25,
      total_tokens: 125,
    })
  })

  it('passes tool ids through on tool_call and tool_result frames', async () => {
    const r = run([
      'event: tool_call\ndata: {"name":"file_read","id":"c-2","args":{"path":"b"}}\n\n',
      'event: tool_result\ndata: {"name":"file_read","id":"c-2","ok":true,"preview":"b"}\n\n',
      'event: done\ndata: {"request_id":"req-4"}\n\n',
    ])
    await r.finished
    expect(r.calls).toEqual([{ name: 'file_read', callId: 'c-2' }])
    expect(r.results).toEqual([{ name: 'file_read', callId: 'c-2', ok: true }])
  })
})
