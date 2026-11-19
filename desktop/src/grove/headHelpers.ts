/** Pure helpers for Remedy's head surfaces (child room, floating bubble).
 * Kept apart from the components so fast refresh stays component-only. */

import type { HeadChild } from '../api/head'
import type { ChatMessage } from '../types'

export interface BubbleMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
}

/** Poll fast while the child works (drafts make that live), slow otherwise. */
export function childPollMs(state: HeadChild['state']): number {
  return state === 'working' || state === 'queued' ? 2000 : 10000
}

/** The words that reach Remedy when the owner types in a child's room. */
export function aboutChild(label: string, text: string): string {
  return `About the side job “${label}”: ${text.trim()}`
}

/** Remedy's messages after `anchorId` ('' = all), user/assistant text only. */
export function bubbleSlice(messages: ChatMessage[], anchorId: string): BubbleMessage[] {
  const idx = anchorId ? messages.findIndex((m) => m.id === anchorId) : -1
  return messages
    .slice(idx + 1)
    .filter(
      (m) =>
        (m.role === 'user' || m.role === 'assistant') &&
        typeof m.content === 'string' &&
        m.content.trim() !== '',
    )
    .map((m) => ({
      id: m.id,
      role: m.role as 'user' | 'assistant',
      // Inside the room it is obvious which job a message is about.
      text: m.role === 'user' ? stripAboutChild(m.content as string) : (m.content as string),
    }))
}

/** Drop the "About the side job …:" prefix that names the job for Remedy. */
export function stripAboutChild(text: string): string {
  return text.replace(/^About the side job “[^”]*”:\s*/, '')
}

export const BUBBLE_W = 340
export const BUBBLE_H = 380

/** Keep the bubble fully on screen for a viewport of (vw, vh). */
export function clampBubble(p: { x: number; y: number }, vw: number, vh: number): { x: number; y: number } {
  const pad = 8
  return {
    x: Math.min(Math.max(pad, p.x), Math.max(pad, vw - BUBBLE_W - pad)),
    y: Math.min(Math.max(pad, p.y), Math.max(pad, vh - 60)),
  }
}

/** Decision order: the oldest first, so the owner answers in the order
 * Remedy asked; internal life-drive rows never surface as cards. */
export function orderDecisions<T extends { id: string; tool_name?: string; created_at?: number; status?: string }>(
  items: T[],
): T[] {
  return items
    .filter((i) => i.tool_name !== 'life_drive' && (i.status === undefined || i.status === 'pending'))
    .slice()
    .sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
}

/** Fields the runtime fills in itself — never shown to the owner as part of
 * what Remedy asked to do. */
const RUNTIME_FIELDS = new Set([
  'caller_origin',
  'session_id',
  'home_dir',
  'workspace_root',
  'project_path',
  'write_roots',
  'owner_confirmed',
  'page_context',
  '_go_bound',
])

/** Reasons that only restate that a card exists — not worth a line. */
export function isGenericReason(reason: string | undefined): boolean {
  const r = (reason || '').trim().toLowerCase()
  return !r || r === 'tool requires your approval' || r === 'approval required'
}

/** What exactly would run, as "label: value" lines when the command is a JSON
 * object (runtime-bound fields dropped), else the raw text. */
export function explainCommand(command: string): { label: string; value: string }[] | string {
  const raw = (command || '').trim()
  if (!raw.startsWith('{')) return raw
  let obj: unknown
  try {
    obj = JSON.parse(raw)
  } catch {
    return raw
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return raw
  const rows: { label: string; value: string }[] = []
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (RUNTIME_FIELDS.has(k)) continue
    if (v === '' || v === null || v === undefined) continue
    const label = k.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
    const value = typeof v === 'string' ? v : JSON.stringify(v)
    rows.push({ label, value })
  }
  return rows.length ? rows : raw
}
