import { apiFetch, getApiBase } from './client'
import type { ChatMessage, ModelDefinition, AgentDefinition, CommandDefinition } from '../types'

export async function listMessages(
  sessionId: string,
  limit = 100,
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

export type StreamHandlers = {
  onToken: (text: string) => void
  onDone: (data: { request_id: string }) => void
  onError: (message: string) => void
  onThinking?: (text: string) => void
  onToolCall?: (name: string, args?: Record<string, unknown>) => void
  onToolResult?: (name: string, preview?: string, ok?: boolean) => void
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
  onDone: (data: { request_id: string }) => void,
  onError: (message: string) => void,
  model?: string,
  onThinking?: (text: string) => void,
  onToolCall?: (name: string, args?: Record<string, unknown>) => void,
  onToolResult?: (name: string, preview?: string, ok?: boolean) => void,
  attachments?: AttachmentPayload[],
  onProgress?: (info: StreamProgress) => void,
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(`${getApiBase()}/sessions/${sessionId}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          model,
          attachments: attachments?.length ? attachments : undefined,
        }),
        signal: controller.signal,
      })

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
              onToolCall?.(payload.name, args)
            }
            break
          case 'tool_result':
            if (typeof payload.name === 'string' && payload.name) {
              onToolResult?.(
                payload.name,
                typeof payload.preview === 'string' ? payload.preview : undefined,
                typeof payload.ok === 'boolean' ? payload.ok : true,
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
          case 'done':
            finished = true
            onDone(payload as { request_id: string })
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
      if (err instanceof Error && err.name !== 'AbortError') {
        onError(err.message)
      }
    }
  })()

  return controller
}

export async function executeCommand(
  sessionId: string,
  command: string,
): Promise<{ text: string; action?: string }> {
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

export async function exportSession(sessionId: string): Promise<{ markdown: string; filename: string }> {
  return apiFetch(`/sessions/${sessionId}/export`)
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
