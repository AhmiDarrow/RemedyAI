import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  apiFetch: vi.fn(),
  authHeaders: () => ({}),
  clearApiToken: () => {},
  ensureApiToken: async () => {},
  formatApiErrorBody: (_body: unknown, fallback: string) => fallback,
  getApiBase: () => 'http://127.0.0.1:7400/api',
}))

import { attachTurn, type StreamDonePayload, type UsagePayload } from './messages'

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

type Capture = {
  tokens: string[]
  calls: { name: string; callId?: string }[]
  results: { name: string; callId?: string; ok?: boolean }[]
  usage: UsagePayload[]
  done: StreamDonePayload[]
  errors: string[]
  seqs: number[]
  starts: string[]
}

function capture(): Capture {
  return {
    tokens: [],
    calls: [],
    results: [],
    usage: [],
    done: [],
    errors: [],
    seqs: [],
    starts: [],
  }
}

function attach(
  out: Capture,
  opts?: { after?: number },
): { finished: Promise<void>; controller: AbortController } {
  let controller!: AbortController
  const finished = new Promise<void>((resolve) => {
    controller = attachTurn(
      's1',
      'req-1',
      {
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
        onToolResult: (name, _preview, ok, callId) =>
          out.results.push({ name, callId, ok }),
        onUsage: (u) => out.usage.push(u),
        onSeq: (n) => out.seqs.push(n),
        onStart: (info) => out.starts.push(info.requestId),
      },
      opts,
    )
  })
  return { finished, controller }
}

/** The frames a turn log replays for a build that read a file and finished. */
const REPLAY = [
  'event: start\ndata: {"request_id":"req-1","claim_epoch":3,"seq":1}\n\n',
  'event: token\ndata: {"text":"Reading ","seq":2}\n\n',
  'event: tool_call\ndata: {"name":"read","id":"call-a","args":{"path":"a.ts"},"seq":3}\n\n',
  'event: tool_result\ndata: {"name":"read","id":"call-a","ok":true,"preview":"1\\tconst","seq":4}\n\n',
  'data: {"type":"usage","prompt_tokens":900,"completion_tokens":40,"cache_read_tokens":8000,"seq":5}\n\n',
  'event: token\ndata: {"text":"done.","seq":6}\n\n',
  'event: done\ndata: {"request_id":"req-1","status":"ok","seq":7}\n\n',
]

const origFetch = globalThis.fetch

describe('attachTurn', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn() as unknown as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = origFetch
    vi.restoreAllMocks()
  })

  it('replays the missed frames in order and ends on the terminal frame', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(sseResponse(REPLAY))
    const out = capture()
    await attach(out).finished

    expect(out.starts).toEqual(['req-1'])
    expect(out.tokens.join('')).toBe('Reading done.')
    expect(out.calls).toEqual([{ name: 'read', callId: 'call-a' }])
    expect(out.results).toEqual([{ name: 'read', callId: 'call-a', ok: true }])
    expect(out.usage[0]).toMatchObject({ cache_read_tokens: 8000 })
    expect(out.seqs).toEqual([1, 2, 3, 4, 5, 6, 7])
    expect(out.done).toHaveLength(1)
    expect(out.done[0]).toMatchObject({ request_id: 'req-1', status: 'ok' })
    expect(out.done[0]?.interrupted).toBeUndefined()
  })

  it('requests the turn log with request_id and no after on a fresh attach', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(sseResponse(REPLAY))
    await attach(capture()).finished
    const url = String(vi.mocked(globalThis.fetch).mock.calls[0]?.[0])
    expect(url).toContain('/sessions/s1/stream/attach')
    expect(url).toContain('request_id=req-1')
    expect(url).not.toContain('after=')
  })

  it('is idempotent: a second attach over the same log adds nothing new', async () => {
    // A body can only be read once — serve a fresh one per attach.
    vi.mocked(globalThis.fetch).mockImplementation(async () => sseResponse(REPLAY))
    const first = capture()
    await attach(first).finished
    // Same client state, resumed from the watermark the first attach reached.
    const second = capture()
    await attach(second, { after: first.seqs.at(-1) }).finished

    expect(second.tokens).toEqual([])
    expect(second.calls).toEqual([])
    expect(second.results).toEqual([])
    expect(second.usage).toEqual([])
  })

  it('resuming twice from scratch replays identically — never doubled', async () => {
    vi.mocked(globalThis.fetch).mockImplementation(async () => sseResponse(REPLAY))
    const a = capture()
    await attach(a).finished
    const b = capture()
    await attach(b).finished
    expect(b.tokens).toEqual(a.tokens)
    expect(b.calls).toEqual(a.calls)
    expect(b.results).toEqual(a.results)
  })

  it('re-syncs from resume_after on a gap instead of missing the tool call', async () => {
    const dropped = [
      'event: start\ndata: {"request_id":"req-1","seq":1}\n\n',
      'event: token\ndata: {"text":"before ","seq":2}\n\n',
      'event: gap\ndata: {"request_id":"req-1","from":3,"to":4,"resume_after":2}\n\n',
      // Anything after the gap notice on this socket must not be applied —
      // reading on would push the watermark past the frames the server lost.
      'event: token\ndata: {"text":"SKIPPED","seq":5}\n\n',
    ]
    const resynced = [
      'event: tool_call\ndata: {"name":"bash","id":"call-b","args":{"command":"npm test"},"seq":3}\n\n',
      'event: tool_result\ndata: {"name":"bash","id":"call-b","ok":true,"preview":"ok","seq":4}\n\n',
      'event: token\ndata: {"text":"after","seq":5}\n\n',
      'event: done\ndata: {"request_id":"req-1","status":"ok","seq":6}\n\n',
    ]
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(sseResponse(dropped))
      .mockResolvedValueOnce(sseResponse(resynced))

    const out = capture()
    await attach(out).finished

    const urls = vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0]))
    expect(urls).toHaveLength(2)
    expect(urls[1]).toContain('after=2')
    expect(out.calls).toEqual([{ name: 'bash', callId: 'call-b' }])
    expect(out.tokens.join('')).toBe('before after')
    expect(out.tokens.join('')).not.toContain('SKIPPED')
    expect(out.done).toHaveLength(1)
  })

  it('treats a socket that closes without a terminal frame as interrupted', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      sseResponse(['event: token\ndata: {"text":"half","seq":1}\n\n']),
    )
    const out = capture()
    await attach(out).finished
    expect(out.tokens).toEqual(['half'])
    expect(out.done[0]).toMatchObject({ request_id: 'req-1', interrupted: true })
  })

  it('maps the server`s interrupted done status onto the interrupted flag', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      sseResponse([
        'event: done\ndata: {"request_id":"req-1","status":"interrupted","seq":9}\n\n',
      ]),
    )
    const out = capture()
    await attach(out).finished
    expect(out.done[0]).toMatchObject({ interrupted: true })
    expect(out.errors).toEqual([])
  })

  it('reports a failed attach as an error rather than a silent idle session', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Turn not found' }), { status: 404 }),
    )
    const out = capture()
    await attach(out).finished
    expect(out.errors).toHaveLength(1)
    expect(out.done).toEqual([])
  })
})
