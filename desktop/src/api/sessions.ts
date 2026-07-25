import { apiFetch } from './client'
import type { ChatSession } from '../types'

export async function listSessions(limit = 200, offset = 0) {
  const data = await apiFetch<{ sessions: ChatSession[] }>(
    `/sessions?limit=${limit}&offset=${offset}`,
  )
  return data.sessions
}

export async function createSession(params: {
  title?: string
  model?: string
  agent?: string
  project_path?: string
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
  updates: { title?: string; model?: string; agent?: string; project_path?: string },
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

/** Export session as plain text (.txt) or markdown (.md). */
export async function exportSession(
  sessionId: string,
  format: 'txt' | 'md' = 'txt',
): Promise<{ text: string; markdown: string; filename: string; format: string }> {
  return apiFetch(`/sessions/${sessionId}/export?format=${format}`)
}

/** Import a session from .txt / .md export text or a server-readable path. */
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
