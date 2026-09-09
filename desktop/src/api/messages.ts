import {
  apiFetch,
  authHeaders,
  clearApiToken,
  ensureApiToken,
  formatApiErrorBody,
  getApiBase,
} from './client'
import { pumpTurnStream, SeqGate } from './turnStream'
import type { TurnStreamHandlers } from './turnStream'
import type { ChatMessage, ModelDefinition, AgentDefinition, CommandDefinition } from '../types'

export type { TurnStreamHandlers } from './turnStream'

/**
 * User-facing message for a failed stream HTTP response.
 * Uses the same FastAPI-aware flattener as apiFetch (validation arrays, detail, …).
 */
export function streamHttpErrorMessage(
  body: unknown,
  status: number,
  statusText = '',
): string {
  return formatApiErrorBody(body, statusText || `HTTP ${status}`)
}

/**
 * After POST /messages 409: join the live turn if we can. Abort/supersede
 * only when /steer says there is no turn (`no_turn`). A full nudge queue
 * or a blip must not stop computer-use.
 */
export function resolveSteer409(result: {
  ok?: boolean
  steered?: boolean
  reason?: string
}): 'steered' | 'retry-steer' | 'supersede' {
  if (result.steered) return 'steered'
  if (result.reason === 'no_turn') return 'supersede'
  return 'retry-steer'
}

/** Fetch/SSE drop (sidecar killed mid-turn) — not an xAI outage. */
export function streamTransportErrorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err || 'Stream failed')
  const low = msg.toLowerCase()
  const dropped =
    low.includes('failed to fetch')
    || low.includes('networkerror')
    || low.includes('network error')
    // Safari's bare fetch failure ("Load failed", possibly "TypeError: Load
    // failed") — but not backend text like "model load failed".
    || low === 'load failed'
    || low.endsWith(': load failed')
    || low.includes('err_connection')
    || low.includes('connection reset')
    || low.includes('connection refused')
    || low.includes('connection aborted')
  if (dropped) {
    return (
      'Lost the local server mid-turn (install or restart). '
      + 'History is intact — send continue.'
    )
  }
  return msg || 'Stream failed'
}

export type SessionTodosPayload = {
  todos: { id: string; content: string; status: string }[]
}

export async function listSessionTodos(sessionId: string): Promise<SessionTodosPayload> {
  return apiFetch<SessionTodosPayload>(`/sessions/${sessionId}/todos`)
}

export async function listMessages(
  sessionId: string,
  /** Newest-window size (server returns latest N in chrono order). */
  limit = 250,
  offset = 0,
): Promise<ChatMessage[]> {
  const data = await apiFetch<{ messages: ChatMessage[] }>(
    `/sessions/${sessionId}/messages?limit=${limit}&offset=${offset}`,
  )
  return data.messages
}

export type UsagePayload = {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  estimated_cost_usd?: number
  /** Prompt input served from the provider's cache (Anthropic prompt caching). */
  cache_read_tokens?: number
  /** Prompt input written into the cache this round. */
  cache_write_tokens?: number
  source?: string
  model?: string | null
  provider?: string | null
}

export type StreamDonePayload = {
  request_id: string
  usage?: UsagePayload
  /** Cooperative Stop / server abort — not a successful completion. */
  aborted?: boolean
  /**
   * The server was still running this session's previous turn (our SSE had
   * dropped, so the UI thought it was idle). The text was handed to that turn
   * as mid-turn steering instead of killing it; no new assistant row follows.
   */
  steered?: boolean
  /**
   * The connection closed without a terminal `done` / `aborted` / `error`
   * frame (proxy cut, sidecar restart, webview reload). The turn may still be
   * running server-side: callers keep the partial text, mark the bubble as
   * interrupted and re-fetch the session instead of treating this as success.
   */
  interrupted?: boolean
}

export type AttachmentPayload = {
  path: string
  name?: string
  mime?: string
  size?: number
  is_image?: boolean
  is_text?: boolean
}

export type StreamProgress = {
  percent?: number | null
  label?: string
  eta?: string | null
  step?: number | null
  total?: number | null
}

export type SendTurnOptions = {
  model?: string
  /** Per-session provider — must pair with model for multi-tab multi-provider. */
  provider?: string
  attachments?: AttachmentPayload[]
  planMode?: boolean
  chatMode?: boolean
}

/** `GET /api/sessions/{id}/stream/attach` URL for a turn, resuming after `after`. */
export function attachStreamUrl(
  sessionId: string,
  requestId: string,
  after = 0,
): string {
  const q = new URLSearchParams({ request_id: requestId })
  if (after > 0) q.set('after', String(Math.trunc(after)))
  return `${getApiBase()}/sessions/${sessionId}/stream/attach?${q.toString()}`
}

/** One authenticated GET on the attach stream (401 → re-bootstrap once). */
async function openAttachStream(url: string, signal: AbortSignal): Promise<Response> {
  await ensureApiToken()
  const doFetch = () =>
    fetch(url, {
      method: 'GET',
      headers: { ...authHeaders(), Accept: 'text/event-stream' },
      signal,
    })
  let res = await doFetch()
  if (res.status === 401) {
    clearApiToken()
    await ensureApiToken()
    res = await doFetch()
  }
  return res
}

/**
 * Follow a turn that is already running: replay what this client missed from
 * the turn log, then tail the live turn until it finishes.
 *
 * Resuming twice is safe — every frame is gated on its `seq` — and a `gap`
 * notice re-opens the stream from the server's `resume_after` instead of
 * continuing past the frames it lost.
 */
export function attachTurn(
  sessionId: string,
  requestId: string,
  handlers: TurnStreamHandlers,
  opts?: { after?: number },
): AbortController {
  const controller = new AbortController()
  const gate = new SeqGate(opts?.after ?? 0)

  ;(async () => {
    let after = Math.max(0, Math.trunc(opts?.after ?? 0))
    try {
      while (!controller.signal.aborted) {
        const res = await openAttachStream(
          attachStreamUrl(sessionId, requestId, after),
          controller.signal,
        )
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          handlers.onError(streamHttpErrorMessage(body, res.status, res.statusText))
          return
        }
        if (!res.body) {
          handlers.onError('No response body from server')
          return
        }
        const outcome = await pumpTurnStream(res.body, handlers, gate, { requestId })
        if (outcome.kind === 'terminal') return
        if (outcome.kind === 'gap') {
          after = outcome.after
          continue
        }
        // Closed with no terminal frame. The turn may still be running server
        // side, but this socket is done: report it as interrupted rather than
        // as a success.
        handlers.onDone({ request_id: requestId, interrupted: true })
        return
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      handlers.onError(streamTransportErrorMessage(err))
    }
  })()

  return controller
}

/**
 * Send a message and stream the turn.
 *
 * A `gap` notice mid-stream means the server's frame queue overflowed: this
 * client abandons the POST socket and finishes the turn on `stream/attach`
 * from the lost range (dropping the POST connection never cancels the turn —
 * only `POST /abort` does), so the caller still sees one continuous stream.
 */
export function streamMessage(
  sessionId: string,
  message: string,
  handlers: TurnStreamHandlers,
  opts: SendTurnOptions = {},
): AbortController {
  const controller = new AbortController()
  const { model, provider, attachments, planMode, chatMode } = opts
  const gate = new SeqGate()
  let requestId = ''
  const wrapped: TurnStreamHandlers = {
    ...handlers,
    // The turn id is what a re-sync needs; everything else is the caller's.
    onStart: (info) => {
      if (info.requestId) requestId = info.requestId
      handlers.onStart?.(info)
    },
  }

  ;(async () => {
    try {
      await ensureApiToken()
      const { sessionSelectHeaders, shouldRetryStreamAfter409 } = await import(
        '../sessions/focusedSession'
      )
      const doFetch = () =>
        fetch(`${getApiBase()}/sessions/${sessionId}/messages/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders(),
            ...sessionSelectHeaders(sessionId),
          },
          body: JSON.stringify({
            message,
            model,
            provider: provider || undefined,
            attachments: attachments?.length ? attachments : undefined,
            plan_mode: Boolean(planMode),
            chat_mode: Boolean(chatMode),
          }),
          signal: controller.signal,
        })

      let res = await doFetch()
      // After update / server restart the cached token can be stale — re-bootstrap once.
      if (res.status === 401) {
        clearApiToken()
        await ensureApiToken()
        res = await doFetch()
      }
      // Same-session already streaming (Stop+send / double-submit). The dying
      // turn keeps its claim until the generator finally runs — retry with backoff.
      // reason=supersede: the server words the interrupted turn's durable row
      // as "interrupted by your next message" instead of a plain Stop.
      if (res.status === 409 && !controller.signal.aborted) {
        // The UI believed the session was idle but the server is still working
        // (SSE dropped mid-turn — connection resets did exactly this during a
        // 3 h build). Killing that turn is the last resort: first hand the words
        // to the running turn as steering. Attachments need a turn of their own.
        if (!attachments?.length) {
          let last: 'steered' | 'retry-steer' | 'supersede' = 'retry-steer'
          for (const wait of [0, 80, 160, 320]) {
            if (wait) {
              await new Promise((r) => setTimeout(r, wait))
            }
            if (controller.signal.aborted) break
            try {
              const sr = await fetch(`${getApiBase()}/sessions/${sessionId}/steer`, {
                method: 'POST',
                headers: {
                  ...authHeaders(),
                  Accept: 'application/json',
                  'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message }),
              })
              const sj = (await sr.json().catch(() => ({}))) as {
                steered?: boolean
                reason?: string
              }
              last = resolveSteer409({
                ok: sr.ok,
                steered: Boolean(sj?.steered),
                reason: sj?.reason,
              })
              if (last === 'steered') {
                handlers.onDone({ request_id: '', steered: true })
                return
              }
              if (last === 'supersede') break
            } catch {
              last = 'retry-steer'
            }
          }
          if (last === 'retry-steer') {
            // Nudge full or a blip — keep the live turn. Words retry next Enter.
            handlers.onDone({ request_id: '', steered: true })
            return
          }
        }
        // no_turn (or attachments): do not abort. A stale Stop-less abort
        // would kill a newer claim that started after steer. Retry POST.
        for (const wait of [80, 160, 320]) {
          await new Promise((r) => setTimeout(r, wait))
          if (controller.signal.aborted) break
          if (!shouldRetryStreamAfter409(sessionId, { aborted: controller.signal.aborted })) {
            break
          }
          res = await doFetch()
          if (res.status !== 409) break
        }
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        handlers.onError(streamHttpErrorMessage(body, res.status, res.statusText))
        return
      }
      if (!res.body) {
        handlers.onError('No response body from server')
        return
      }

      let outcome = await pumpTurnStream(res.body, wrapped, gate)
      // Frames were lost. Finish the turn on the turn log rather than carry on
      // past the hole; the turn itself keeps running server-side.
      while (outcome.kind === 'gap' && requestId && !controller.signal.aborted) {
        const resumed = await openAttachStream(
          attachStreamUrl(sessionId, requestId, outcome.after),
          controller.signal,
        )
        if (!resumed.ok || !resumed.body) {
          handlers.onDone({ request_id: requestId, interrupted: true })
          return
        }
        outcome = await pumpTurnStream(resumed.body, wrapped, gate, { requestId })
      }
      if (outcome.kind === 'terminal') return
      // The stream closed with no done/aborted/error frame. That is not a
      // completed turn: the sidecar may have restarted, a proxy may have cut
      // us, or the turn may still be running server-side. Report it as
      // interrupted so the caller keeps the partial, marks the bubble and
      // re-fetches the session — never as a success with an empty request id.
      handlers.onDone({ request_id: requestId, interrupted: true })
    } catch (err: unknown) {
      // AbortError: user Stop or session switch — callers clear streaming state.
      // Do not call onDone (would race setMessages onto the wrong session).
      if (err instanceof Error && err.name === 'AbortError') {
        return
      }
      handlers.onError(streamTransportErrorMessage(err))
    }
  })()

  return controller
}

export async function executeCommand(
  sessionId: string,
  command: string,
): Promise<{ text: string; action?: string; session_id?: string }> {
  return apiFetch(`/sessions/${sessionId}/command`, {
    method: 'POST',
    body: JSON.stringify({ command }),
  })
}

export async function listModels(): Promise<{
  models: ModelDefinition[]
  default: string
}> {
  return apiFetch('/models')
}

export async function listAgents(): Promise<{ agents: AgentDefinition[] }> {
  return apiFetch('/agents')
}

export async function listCommands(): Promise<{ commands: CommandDefinition[] }> {
  return apiFetch('/commands')
}

export async function searchFiles(query: string): Promise<{
  query: string
  results: { name: string; path: string; is_dir: boolean }[]
}> {
  if (!query) return { query: '', results: [] }
  return apiFetch(`/files/search?query=${encodeURIComponent(query)}`)
}

/** Soft-delete a user message and all later messages; returns text for edit+resend. */
export async function editFromMessageApi(
  sessionId: string,
  msgId: string,
): Promise<{ status: string; content: string; reverted_count: number }> {
  return apiFetch(`/sessions/${sessionId}/messages/${msgId}/edit`, {
    method: 'POST',
  })
}

export async function exportSession(
  sessionId: string,
  format: 'txt' | 'md' = 'txt',
): Promise<{ text: string; markdown: string; filename: string; format: string }> {
  return apiFetch(`/sessions/${sessionId}/export?format=${format}`)
}

/** Create a session from a plain-text or legacy markdown export body. */
export async function importSession(params: {
  text?: string
  path?: string
  title?: string
  model?: string
  agent?: string
  project_path?: string
}): Promise<{
  id: string
  title: string
  model?: string | null
  agent?: string | null
  project_path?: string | null
  message_count: number
  imported_messages: number
  created_at?: string | null
  updated_at?: string | null
}> {
  return apiFetch('/sessions/import', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function scanProject(path = '.'): Promise<{
  path: string
  file_counts: Record<string, number>
  top_files: Record<string, string[]>
  python_deps: string
  js_deps: string
}> {
  return apiFetch(`/projects/scan?path=${encodeURIComponent(path)}`, { method: 'POST' })
}
