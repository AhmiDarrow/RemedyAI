/**
 * Realtime session sync — SSE from GET /api/events/sessions.
 * Messenger (and other) surfaces publish session_created / message_added;
 * desktop refreshes the sidebar and active thread without polling alone.
 */

import { authHeaders, ensureApiToken } from './client'

const SERVER_URL = 'http://127.0.0.1:7400'

function eventsUrl(): string {
  if (typeof window !== 'undefined') {
    const w = window as Window & { __TAURI__?: unknown; __TAURI_INTERNALS__?: unknown }
    if (w.__TAURI__ || w.__TAURI_INTERNALS__) {
      return `${SERVER_URL}/api/events/sessions`
    }
  }
  return '/api/events/sessions'
}

export type SessionSyncEvent = {
  type: string
  session_id?: string
  origin_channel?: string | null
  message_id?: string | null
  title?: string | null
  message_count?: number | null
  role?: string | null
  ts?: number
}

export type SessionEventsHandlers = {
  onEvent?: (ev: SessionSyncEvent) => void
  onHello?: () => void
  onError?: (err: unknown) => void
}

/**
 * Subscribe to session events. Uses fetch+ReadableStream because EventSource
 * cannot set Authorization headers (desktop API requires Bearer token).
 * Returns an abort function.
 */
export function subscribeSessionEvents(handlers: SessionEventsHandlers): () => void {
  const ac = new AbortController()
  let stopped = false
  let backoffMs = 1500

  const run = async () => {
    while (!stopped && !ac.signal.aborted) {
      try {
        await ensureApiToken()
        const headers = authHeaders()
        const res = await fetch(eventsUrl(), {
          method: 'GET',
          headers: {
            ...headers,
            Accept: 'text/event-stream',
          },
          signal: ac.signal,
        })
        if (!res.ok || !res.body) {
          handlers.onError?.(new Error(`session events HTTP ${res.status}`))
          await sleep(backoffMs)
          backoffMs = Math.min(30_000, Math.floor(backoffMs * 1.6))
          continue
        }
        backoffMs = 1500
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buf = ''
        while (!stopped && !ac.signal.aborted) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const parts = buf.split('\n\n')
          buf = parts.pop() || ''
          for (const block of parts) {
            parseSseBlock(block, handlers)
          }
        }
      } catch (e) {
        if (ac.signal.aborted || stopped) break
        handlers.onError?.(e)
        await sleep(backoffMs)
        backoffMs = Math.min(30_000, Math.floor(backoffMs * 1.6))
      }
    }
  }

  void run()
  return () => {
    stopped = true
    ac.abort()
  }
}

function parseSseBlock(block: string, handlers: SessionEventsHandlers): void {
  const lines = block.split('\n')
  let eventName = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (!dataLines.length) return
  try {
    const data = JSON.parse(dataLines.join('\n')) as SessionSyncEvent
    const type = data.type || eventName
    if (type === 'hello') {
      handlers.onHello?.()
      return
    }
    handlers.onEvent?.({ ...data, type })
  } catch {
    /* ignore malformed */
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
