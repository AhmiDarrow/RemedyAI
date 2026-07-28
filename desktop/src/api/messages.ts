import { apiFetch, authHeaders, ensureApiToken, getApiBase } from './client'
import type { ChatMessage, ModelDefinition, AgentDefinition, CommandDefinition } from '../types'

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

export async function sendMessage(
  sessionId: string,
  message: string,
  model?: string,
): Promise<{ response: string; request_id: string }> {
  return apiFetch(`/sessions/${sessionId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ message, model }),
  })
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

export type StreamHandlers = {
  onToken: (text: string) => void
  onDone: (data: { request_id: string; usage?: UsagePayload }) => void
  onError: (message: string) => void
  onThinking?: (text: string) => void
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
  onDone: (data: { request_id: string; usage?: UsagePayload }) => void,
  onError: (message: string) => void,
  model?: string,
  onThinking?: (text: string) => void,
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
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      await ensureApiToken()
      const doFetch = () =>
        fetch(`${getApiBase()}/sessions/${sessionId}/messages/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...authHeaders(),
          },
          body: JSON.stringify({
            message,
            model,
            provider: provider || undefined,
            attachments: attachments?.length ? attachments : undefined,
            plan_mode: Boolean(planMode),
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

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        onError(
          (body as { detail?: string; message?: string; error?: string })?.detail
            || (body as { message?: string })?.message
            || (body as { error?: string })?.error
            || res.statusText
            || `HTTP ${res.status}`,
        )
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
            if (typeof payload.text === 'string' && payload.text) onThinking?.(payload.text)
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
          case 'done':
            finished = true
            if (payload.usage && typeof payload.usage === 'object') {
              onUsage?.(payload.usage as UsagePayload)
            }
            onDone(payload as { request_id: string; usage?: UsagePayload })
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
      // If stream closed without a done/error event, still complete cleanly.
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

/** @deprecated use editFromMessageApi — kept for older call sites */
export async function revertMessageApi(
  sessionId: string,
  msgId: string,
): Promise<{ status: string; content?: string }> {
  return editFromMessageApi(sessionId, msgId)
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

export async function listCustomCommands(): Promise<{ commands: { name: string; description: string; file: string }[] }> {
  return apiFetch('/commands/custom')
}

export async function listCustomAgents(): Promise<{ agents: { name: string; description: string; file: string }[] }> {
  return apiFetch('/agents/custom')
}

export async function getCustomCommand(name: string): Promise<{ content: string }> {
  return apiFetch(`/commands/custom/${encodeURIComponent(name)}`)
}

export async function getCustomAgent(name: string): Promise<{ content: string }> {
  return apiFetch(`/agents/custom/${encodeURIComponent(name)}`)
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
