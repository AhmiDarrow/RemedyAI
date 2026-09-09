/**
 * Shared turn-stream plumbing for the live POST stream and `stream/attach`.
 *
 * Both endpoints emit byte-identical SSE frames built from the same turn-log
 * record, so both go through one router here. Two invariants live in this
 * module:
 *
 * - **Seq.** Every logged frame carries a monotonic per-turn `seq`. A replay
 *   that overlaps the live tail must be harmless, so a frame is applied at
 *   most once (see `SeqGate`).
 * - **Gap.** When the server's frame queue overflows it drops the oldest
 *   frames and says so with `event: gap` — `{from, to, resume_after}`. That is
 *   the signal to stop reading the current socket and re-attach from the turn
 *   log instead of silently missing tool calls.
 */

import { createSseParser } from './sse'
import type { StreamDonePayload, StreamProgress, UsagePayload } from './messages'

/** Numeric field of a frame payload (0 when absent or unparsable). */
function num(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : 0
}

/** Seq of a frame; 0 for transport-only frames (`gap`) that are not logged. */
export function frameSeq(payload: Record<string, unknown>): number {
  const n = num(payload.seq)
  return n > 0 ? Math.trunc(n) : 0
}

/**
 * Applied-once filter over per-turn seqs.
 *
 * `floor` is the watermark below which everything has been applied; seqs
 * applied above a hole are remembered individually until the hole fills. A
 * dropped frame therefore leaves the floor parked exactly where the server's
 * `resume_after` points, which is what the re-attach asks for.
 */
export class SeqGate {
  private floorSeq: number
  private readonly ahead = new Set<number>()

  constructor(after = 0) {
    this.floorSeq = Math.max(0, Math.trunc(after) || 0)
  }

  /** Highest seq with no hole below it — the resume point for a re-attach. */
  get floor(): number {
    return this.floorSeq
  }

  /**
   * True when this frame has not been applied yet. Frames without a seq
   * (transport notices, pre-seq sidecars) always pass.
   */
  admit(seq: number): boolean {
    if (!seq) return true
    if (seq <= this.floorSeq || this.ahead.has(seq)) return false
    this.ahead.add(seq)
    while (this.ahead.delete(this.floorSeq + 1)) this.floorSeq += 1
    return true
  }
}

/** `event: gap` payload — the seq range the server could not deliver. */
export type GapNotice = { from: number; to: number; resumeAfter: number }

export function readGapFrame(payload: Record<string, unknown>): GapNotice {
  const from = Math.max(0, Math.trunc(num(payload.from)))
  const to = Math.max(from, Math.trunc(num(payload.to)))
  const resumeRaw = payload.resume_after
  const resumeAfter =
    resumeRaw == null ? Math.max(0, from - 1) : Math.max(0, Math.trunc(num(resumeRaw)))
  return { from, to, resumeAfter }
}

/**
 * Normalize a `usage` frame.
 *
 * The Go runtime spreads the provider's own payload (`prompt_tokens`,
 * `cache_read_tokens`, …); the design's shorthand is `in` / `out` /
 * `cache_read` / `cache_write`. Accept both so neither wire shape loses the
 * cache counters — a cache hit is the difference between an expensive build
 * and a cheap one.
 */
export function readUsageFrame(payload: Record<string, unknown>): UsagePayload {
  const pick = (...keys: string[]): number => {
    for (const k of keys) {
      const v = payload[k]
      if (typeof v === 'number' && Number.isFinite(v)) return v
      if (typeof v === 'string' && v.trim() && Number.isFinite(Number(v))) return Number(v)
    }
    return 0
  }
  const promptTokens = pick('prompt_tokens', 'input_tokens', 'in')
  const completionTokens = pick('completion_tokens', 'output_tokens', 'out')
  const totalRaw = pick('total_tokens', 'total')
  const cacheRead = pick('cache_read_tokens', 'cache_read_input_tokens', 'cache_read')
  const cacheWrite = pick('cache_creation_input_tokens', 'cache_write_tokens', 'cache_write')
  return {
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    // Go `usage` frames carry only in/out — total is derived.
    total_tokens: totalRaw || promptTokens + completionTokens,
    estimated_cost_usd:
      typeof payload.estimated_cost_usd === 'number' ? payload.estimated_cost_usd : 0,
    cache_read_tokens: cacheRead || undefined,
    cache_write_tokens: cacheWrite || undefined,
    source: typeof payload.source === 'string' ? payload.source : undefined,
    model: typeof payload.model === 'string' ? payload.model : null,
    provider: typeof payload.provider === 'string' ? payload.provider : null,
  }
}

/** Callbacks a turn stream drives. Every one is optional except the terminals. */
export type TurnStreamHandlers = {
  onToken: (text: string) => void
  onDone: (data: StreamDonePayload) => void
  onError: (message: string) => void
  onThinking?: (text: string, meta?: { replace?: boolean }) => void
  onToolCall?: (name: string, args?: Record<string, unknown>, callId?: string) => void
  onToolResult?: (
    name: string,
    preview?: string,
    ok?: boolean,
    callId?: string,
  ) => void
  onProgress?: (info: StreamProgress) => void
  onUsage?: (usage: UsagePayload) => void
  onLibrarySuggest?: (payload: Record<string, unknown>) => void
  onTodos?: (payload: Record<string, unknown>) => void
  onLifeTask?: (payload: Record<string, unknown>) => void
  /**
   * Turn identity from the `start` frame. The request id is what a re-attach
   * and the tool-evidence route are keyed on, so callers record it.
   */
  onStart?: (info: { requestId: string; claimEpoch?: number }) => void
  /** Highest seq applied so far — persist it so a reconnect resumes exactly. */
  onSeq?: (seq: number) => void
}

/** What a socket read ended on. */
export type TurnStreamOutcome =
  /** A `done` / `aborted` / `error` frame arrived; the caller already got it. */
  | { kind: 'terminal' }
  /** The server lost frames; re-attach from `after`. */
  | { kind: 'gap'; after: number; notice: GapNotice }
  /** The socket closed with no terminal frame. */
  | { kind: 'closed' }

type RouterState = {
  gate: SeqGate
  requestId: string
  finished: boolean
  gap: GapNotice | null
}

/** Terminal `done` status → the flags callers act on. */
function doneFlags(status: string): Pick<StreamDonePayload, 'aborted' | 'interrupted'> {
  const s = status.trim().toLowerCase()
  if (s === 'aborted') return { aborted: true }
  if (s === 'interrupted') return { interrupted: true }
  return {}
}

function routeFrame(
  event: string,
  payload: Record<string, unknown>,
  handlers: TurnStreamHandlers,
  state: RouterState,
): void {
  if (state.finished || state.gap) return

  // A gap notice carries no seq and must stop this socket immediately: reading
  // on would advance the watermark past the frames the server just lost.
  if (event === 'gap') {
    state.gap = readGapFrame(payload)
    return
  }

  const seq = frameSeq(payload)
  if (!state.gate.admit(seq)) return
  if (seq) handlers.onSeq?.(state.gate.floor)

  switch (event) {
    case 'token':
      if (typeof payload.text === 'string' && payload.text) handlers.onToken(payload.text)
      break
    case 'thinking':
      if (typeof payload.text === 'string' && payload.text) {
        handlers.onThinking?.(payload.text, { replace: payload.replace === true })
      }
      break
    case 'tool_call': {
      if (typeof payload.name !== 'string' || !payload.name) break
      const args =
        payload.args && typeof payload.args === 'object' && !Array.isArray(payload.args)
          ? (payload.args as Record<string, unknown>)
          : undefined
      handlers.onToolCall?.(payload.name, args, readCallId(payload))
      break
    }
    case 'tool_result':
      if (typeof payload.name !== 'string' || !payload.name) break
      handlers.onToolResult?.(
        payload.name,
        typeof payload.preview === 'string' ? payload.preview : undefined,
        typeof payload.ok === 'boolean' ? payload.ok : true,
        readCallId(payload),
      )
      break
    case 'progress':
      handlers.onProgress?.({
        percent: typeof payload.percent === 'number' ? payload.percent : null,
        label: typeof payload.label === 'string' ? payload.label : undefined,
        eta: typeof payload.eta === 'string' ? payload.eta : null,
        step: typeof payload.step === 'number' ? payload.step : null,
        total: typeof payload.total === 'number' ? payload.total : null,
      })
      break
    case 'usage':
      handlers.onUsage?.(readUsageFrame(payload))
      break
    case 'library_suggest':
      handlers.onLibrarySuggest?.(payload)
      break
    case 'todos':
      handlers.onTodos?.(payload)
      break
    case 'life_task':
      handlers.onLifeTask?.(payload)
      break
    case 'start': {
      const rid = typeof payload.request_id === 'string' ? payload.request_id : ''
      if (rid) state.requestId = rid
      const epoch = typeof payload.claim_epoch === 'number' ? payload.claim_epoch : undefined
      handlers.onStart?.({ requestId: rid, claimEpoch: epoch })
      break
    }
    case 'done': {
      state.finished = true
      if (payload.usage && typeof payload.usage === 'object') {
        handlers.onUsage?.(readUsageFrame(payload.usage as Record<string, unknown>))
      }
      const rid =
        typeof payload.request_id === 'string' && payload.request_id
          ? payload.request_id
          : state.requestId
      const status = typeof payload.status === 'string' ? payload.status : ''
      if (status.trim().toLowerCase() === 'error') {
        handlers.onError(String(payload.error || 'Turn failed'))
        break
      }
      handlers.onDone({ ...(payload as StreamDonePayload), request_id: rid, ...doneFlags(status) })
      break
    }
    case 'aborted':
      // Cooperative Stop — not an error, but not success either.
      state.finished = true
      handlers.onDone({
        request_id:
          typeof payload.request_id === 'string' ? payload.request_id : state.requestId,
        aborted: true,
      })
      break
    case 'error':
      state.finished = true
      handlers.onError(String(payload.message || 'Unknown error'))
      break
  }
}

function readCallId(payload: Record<string, unknown>): string | undefined {
  if (typeof payload.call_id === 'string') return payload.call_id
  if (typeof payload.id === 'string') return payload.id
  return undefined
}

/**
 * Read one SSE body to its end (or to the frame that ends the turn).
 *
 * The caller owns reconnection: on `{kind:'gap'}` it re-opens the turn from
 * `after`; on `{kind:'closed'}` it decides whether the turn was interrupted.
 */
export async function pumpTurnStream(
  body: ReadableStream<Uint8Array>,
  handlers: TurnStreamHandlers,
  gate: SeqGate,
  seed?: { requestId?: string },
): Promise<TurnStreamOutcome> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  const state: RouterState = {
    gate,
    requestId: seed?.requestId || '',
    finished: false,
    gap: null,
  }
  const parser = createSseParser((event, payload) => routeFrame(event, payload, handlers, state))
  try {
    while (!state.finished && !state.gap) {
      const { done, value } = await reader.read()
      if (done) break
      parser.push(decoder.decode(value, { stream: true }))
    }
  } finally {
    if (!state.finished && !state.gap) {
      parser.push(decoder.decode())
      parser.end()
    }
    try {
      await reader.cancel()
    } catch {
      /* the socket is already gone */
    }
  }
  if (state.gap) {
    // Resume from our own watermark when it sits below the server's hint: it
    // is never higher (dropped frames are always ahead of applied ones) and it
    // is the only value that guarantees no gap and no duplicate.
    return {
      kind: 'gap',
      after: Math.min(state.gap.resumeAfter, gate.floor),
      notice: state.gap,
    }
  }
  if (state.finished) return { kind: 'terminal' }
  return { kind: 'closed' }
}
