import { apiFetch } from './client'
import type { ChatSession } from '../types'

export type SessionListPage = {
  sessions: ChatSession[]
  offset: number
  limit: number
  has_more: boolean
}

export async function listSessions(limit = 100, offset = 0): Promise<SessionListPage> {
  const data = await apiFetch<SessionListPage & { sessions: ChatSession[] }>(
    `/sessions?limit=${limit}&offset=${offset}`,
  )
  return {
    sessions: data.sessions || [],
    offset: data.offset ?? offset,
    limit: data.limit ?? limit,
    has_more: Boolean(data.has_more),
  }
}

/** @deprecated prefer listSessions page shape; kept for simple callers */
export async function listSessionsFlat(limit = 100, offset = 0): Promise<ChatSession[]> {
  const page = await listSessions(limit, offset)
  return page.sessions
}

export async function bulkSetSessionProject(
  sessionIds: string[],
  projectPath: string | null,
): Promise<{ updated: string[]; missing: string[]; project_path: string | null }> {
  return apiFetch('/sessions/bulk-project', {
    method: 'POST',
    body: JSON.stringify({
      session_ids: sessionIds,
      project_path: projectPath ?? '',
    }),
  })
}

export async function createSession(params: {
  title?: string
  model?: string
  agent?: string
  project_path?: string
  /** Stamp at create so multi-session tabs keep independent providers. */
  llm_provider?: string
  /** "grove" marks the personal home chat (hidden from Studio's list). */
  origin_channel?: string
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

/**
 * Say something to a turn that is already running. The words join the live
 * loop at its next step — no stop, no restart. `steered: false` means no
 * turn was running; send normally instead.
 */
export async function steerSession(
  sessionId: string,
  message: string,
): Promise<{ steered: boolean }> {
  return apiFetch(`/sessions/${sessionId}/steer`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  })
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
