import { apiFetch } from './client'
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

export function streamMessage(
  sessionId: string,
  message: string,
  onToken: (text: string) => void,
  onDone: (data: { request_id: string }) => void,
  onError: (message: string) => void,
  model?: string,
  onThinking?: (text: string) => void,
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(`/api/sessions/${sessionId}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, model }),
        signal: controller.signal,
      })

      const reader = res.body?.getReader()
      if (!reader) return

      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = ''

      function processEvents() {
        const lines = buffer.split('\n')
        buffer = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            try {
              const payload = JSON.parse(line.slice(6))
              switch (currentEvent) {
                case 'token':
                  if (payload.text) onToken(payload.text)
                  break
                case 'thinking':
                  if (payload.text) onThinking?.(payload.text)
                  break
                case 'done':
                  onDone(payload)
                  break
                case 'error':
                  onError(payload.message || 'Unknown error')
                  break
              }
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
      processEvents()
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
