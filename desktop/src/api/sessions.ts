import { apiFetch } from './client'
import type { ChatSession } from '../types'

export async function listSessions(limit = 50, offset = 0) {
  const data = await apiFetch<{ sessions: ChatSession[] }>(
    `/sessions?limit=${limit}&offset=${offset}`,
  )
  return data.sessions
}

export async function createSession(params: {
  title?: string
  model?: string
  agent?: string
}): Promise<ChatSession> {
  return apiFetch<ChatSession>('/sessions', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function getSession(sessionId: string): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/sessions/${sessionId}`)
}

export async function updateSession(
  sessionId: string,
  updates: { title?: string; model?: string; agent?: string },
): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/sessions/${sessionId}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  })
}

export async function deleteSession(sessionId: string): Promise<void> {
  await apiFetch(`/sessions/${sessionId}`, { method: 'DELETE' })
}

export async function abortSession(sessionId: string): Promise<void> {
  await apiFetch(`/sessions/${sessionId}/abort`, { method: 'POST' })
}
