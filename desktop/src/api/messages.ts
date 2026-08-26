import {
  apiFetch,
  authHeaders,
  ensureApiToken,
  formatApiErrorBody,
  getApiBase,
} from './client'
import type { ChatMessage, ModelDefinition, AgentDefinition, CommandDefinition } from '../types'

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
}

export type StreamHandlers = {
  onToken: (text: string) => void
  onDone: (data: StreamDonePayload) => void
  onError: (message: string) => void
  onThinking?: (text: string, meta?: { replace?: boolean }) => void
  onToolCall?: (
    name: string,
    args?: Record<string, unknown>,
    callId?: string,
  ) => void
  onToolResult?: (
    name: string,
    preview?: string,
    ok?: boolean,
    callId?: string,
  ) => void
  onUsage?: (usage: UsagePayload) => void
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

export function streamMessage(
  sessionId: string,
  message: string,
  onToken: (text: string) => void,
  onDone: (data: StreamDonePayload) => void,
  onError: (message: string) => void,
  model?: string,
  onThinking?: (text: string, meta?: { replace?: boolean }) => void,
  onToolCall?: (
    name: string,
    args?: Record<string, unknown>,
    callId?: string,
  ) => void,
  onToolResult?: (
    name: string,
    preview?: string,
    ok?: boolean,
    callId?: string,
  ) => void,
  attachments?: AttachmentPayload[],
  onProgress?: (info: StreamProgress) => void,
  planMode?: boolean,
  onUsage?: (usage: UsagePayload) => void,
  onLibrarySuggest?: (payload: Record<string, unknown>) => void,
  /** Per-session provider — must pair with model for multi-tab multi-provider. */
  provider?: string,
  onTodos?: (payload: Record<string, unknown>) => void,
  chatMode?: boolean,
): AbortController {
  const controller = new AbortController()

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
        const { clearApiToken, ensureApiToken: reAuth } = await import('./client')
        clearApiToken()
        await reAuth()
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
          try {
            const sr = await fetch(`${getApiBase()}/sessions/${sessionId}/steer`, {
              method: 'POST',
              headers: { ...authHeaders(), Accept: 'application/json', 'Content-Type': 'application/json' },
              body: JSON.stringify({ message }),
            })
            if (sr.ok) {
              const sj = (await sr.json().catch(() => ({}))) as { steered?: boolean }
              if (sj?.steered) {
                onDone({ request_id: '', steered: true })
                return
              }
            }
          } catch {
            /* fall through to supersede */
          }
        }
        try {
          await fetch(`${getApiBase()}/sessions/${sessionId}/abort?reason=supersede`, {
            method: 'POST',
            headers: { ...authHeaders(), Accept: 'application/json' },
          })
        } catch {
          /* best effort */
        }
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
        onError(streamHttpErrorMessage(body, res.status, res.statusText))
        return
      }

      const reader = res.body?.getReader()
      if (!reader) {
        onError('No response body from server')
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = ''
      let finished = false

      function handlePayload(payload: Record<string, unknown>) {
        if (finished) return
        switch (currentEvent) {
          case 'token':
            if (typeof payload.text === 'string' && payload.text) onToken(payload.text)
            break
          case 'thinking':
            if (typeof payload.text === 'string' && payload.text) {
              onThinking?.(payload.text, {
                replace: payload.replace === true,
              })
            }
            break
          case 'tool_call':
            if (typeof payload.name === 'string' && payload.name) {
              const args =
                payload.args && typeof payload.args === 'object' && !Array.isArray(payload.args)
                  ? (payload.args as Record<string, unknown>)
                  : undefined
              const callId =
                typeof payload.call_id === 'string'
                  ? payload.call_id
                  : typeof payload.id === 'string'
                    ? payload.id
                    : undefined
              onToolCall?.(payload.name, args, callId)
            }
            break
          case 'tool_result':
            if (typeof payload.name === 'string' && payload.name) {
              const callId =
                typeof payload.call_id === 'string'
                  ? payload.call_id
                  : typeof payload.id === 'string'
                    ? payload.id
                    : undefined
              onToolResult?.(
                payload.name,
                typeof payload.preview === 'string' ? payload.preview : undefined,
                typeof payload.ok === 'boolean' ? payload.ok : true,
                callId,
              )
            }
            break
          case 'progress':
            onProgress?.({
              percent: typeof payload.percent === 'number' ? payload.percent : null,
              label: typeof payload.label === 'string' ? payload.label : undefined,
              eta: typeof payload.eta === 'string' ? payload.eta : null,
              step: typeof payload.step === 'number' ? payload.step : null,
              total: typeof payload.total === 'number' ? payload.total : null,
            })
            break
          case 'usage':
            onUsage?.({
              prompt_tokens: typeof payload.prompt_tokens === 'number' ? payload.prompt_tokens : 0,
              completion_tokens:
                typeof payload.completion_tokens === 'number' ? payload.completion_tokens : 0,
              total_tokens: typeof payload.total_tokens === 'number' ? payload.total_tokens : 0,
              estimated_cost_usd:
                typeof payload.estimated_cost_usd === 'number' ? payload.estimated_cost_usd : 0,
              source: typeof payload.source === 'string' ? payload.source : undefined,
              model: typeof payload.model === 'string' ? payload.model : null,
              provider: typeof payload.provider === 'string' ? payload.provider : null,
            })
            break
          case 'library_suggest':
            if (payload && typeof payload === 'object') {
              onLibrarySuggest?.(payload)
            }
            break
          case 'todos':
            if (payload && typeof payload === 'object') {
              onTodos?.(payload)
            }
            break
          case 'done':
            finished = true
            if (payload.usage && typeof payload.usage === 'object') {
              onUsage?.(payload.usage as UsagePayload)
            }
            onDone(payload as StreamDonePayload)
            break
          case 'aborted':
            // Cooperative Stop — not an error, but not success either.
            // Callers complete the job as 'aborted' and mark partials [Stopped].
            finished = true
            onDone({
              request_id:
                typeof payload.request_id === 'string' ? payload.request_id : '',
              aborted: true,
            })
            break
          case 'error':
            finished = true
            onError(String(payload.message || 'Unknown error'))
            break
        }
      }

      function processEvents() {
        // Keep incomplete trailing line in buffer (critical for correct SSE framing).
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const raw of lines) {
          const line = raw.replace(/\r$/, '')
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6)) as Record<string, unknown>
              handlePayload(payload)
            } catch {
              // skip unparseable lines
            }
            currentEvent = ''
          }
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        processEvents()
      }
      buffer += decoder.decode()
      if (buffer.trim()) {
        buffer += '\n'
        processEvents()
      }
      // If stream closed without a done/error/aborted event, still complete cleanly.
      // Cooperative abort (event:aborted) leaves finished=true without onDone —
      // callers (stop / interrupt) own job + UI cleanup.
      if (!finished) {
        finished = true
        onDone({ request_id: '' })
      }
    } catch (err: unknown) {
      // AbortError: user Stop or session switch — callers clear streaming state.
      // Do not call onDone (would race setMessages onto the wrong session).
      if (err instanceof Error && err.name === 'AbortError') {
        return
      }
      onError(err instanceof Error ? err.message : String(err || 'Stream failed'))
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
