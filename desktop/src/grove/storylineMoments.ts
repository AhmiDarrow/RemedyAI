/** Storyline: fold a chat session into a co-written story of "moments".
 *
 * Words, actions, and proof are the same kind of thing — blocks on one thread
 * (docs/LIFE_TASK_PARTNER.md). Pure functions; rendered by GroveApp's
 * Storyline tab and unit-tested directly.
 */

import type { ChatMessage, ToolCall } from '../types'

export type MomentKind = 'you-said' | 'remedy-said' | 'remedy-did'

export interface Moment {
  id: string
  kind: MomentKind
  /** Plain-language body (never raw tool JSON). */
  text: string
  /** ISO timestamp from the source message. */
  ts: string
  /** Optional secondary line (e.g. tool detail suffix). */
  sub?: string
}

/** Tool name → plain-language verb phrase for "Remedy did" moments. */
export function describeToolCall(call: ToolCall): string | null {
  const name = (call?.name || '').toLowerCase()
  const args = call?.args || {}
  const str = (k: string): string => {
    const v = args[k]
    return typeof v === 'string' ? v : ''
  }
  const host = (u: string): string => {
    try {
      return new URL(u).hostname.replace(/^www\./, '')
    } catch {
      return u
    }
  }
  switch (name) {
    case 'computer_navigate':
      return str('url') ? `Opened ${host(str('url'))}` : 'Opened a page'
    case 'computer_act': {
      const bits: string[] = []
      if (str('url')) bits.push(`went to ${host(str('url'))}`)
      if (str('click')) bits.push(`pressed “${str('click')}”`)
      if (str('type')) bits.push('typed into the page')
      if (str('key')) bits.push(`pressed ${str('key')}`)
      if (!bits.length) return 'Worked the page'
      const s = bits.join(', ')
      return s.charAt(0).toUpperCase() + s.slice(1)
    }
    case 'computer_click':
      return str('text')
        ? `Pressed “${str('text')}”`
        : 'Clicked on the page'
    case 'computer_type':
      return 'Typed into the focused field'
    case 'computer_key':
      return str('key') ? `Pressed ${str('key')}` : 'Pressed a key'
    case 'computer_app':
      return str('app') ? `Opened ${str('app')} on this PC` : 'Opened an app'
    case 'computer_screenshot':
    case 'computer_snapshot':
    case 'computer_page_text':
    case 'computer_find':
      return 'Looked at the screen'
    case 'vault_list':
      return 'Checked which stored secrets exist (never their values)'
    case 'file_write':
    case 'file_edit':
      return str('path') ? `Worked on ${str('path')}` : 'Worked on a file'
    case 'bash_exec':
    case 'host_run':
      return 'Ran a command on this PC'
    case 'mail_send':
      return 'Sent an email (with your go-ahead)'
    case 'web_search':
      return str('query') ? `Searched the web for “${str('query')}”` : 'Searched the web'
    default:
      return null
  }
}

/** Tools that are pure observation — folded away unless nothing else happened. */
const QUIET_TOOLS = new Set([
  'computer_screenshot',
  'computer_snapshot',
  'computer_page_text',
  'computer_find',
  'computer_wait',
  'computer_monitors',
  'help_list',
  'file_read',
])

export function messagesToMoments(messages: ChatMessage[]): Moment[] {
  const out: Moment[] = []
  for (const m of messages || []) {
    if (!m || m.reverted) continue
    if (m.role === 'user') {
      const text = (m.content || '').trim()
      if (text) {
        out.push({ id: `${m.id}:u`, kind: 'you-said', text, ts: m.created_at })
      }
      continue
    }
    if (m.role !== 'assistant') continue
    // Actions first (they happened before the reply landed)
    const seen = new Set<string>()
    for (let i = 0; i < (m.tool_calls || []).length; i++) {
      const call = m.tool_calls[i]
      if (QUIET_TOOLS.has((call?.name || '').toLowerCase())) continue
      const text = describeToolCall(call)
      if (!text || seen.has(text)) continue
      seen.add(text)
      out.push({ id: `${m.id}:t${i}`, kind: 'remedy-did', text, ts: m.created_at })
    }
    const text = (m.content || '').trim()
    if (text) {
      out.push({ id: `${m.id}:a`, kind: 'remedy-said', text, ts: m.created_at })
    }
  }
  return out
}

/** Compact caption pair for the Alongside talk strip. */
export function latestExchange(
  messages: ChatMessage[],
): { you: string | null; remedy: string | null } {
  let you: string | null = null
  let remedy: string | null = null
  for (let i = (messages || []).length - 1; i >= 0; i--) {
    const m = messages[i]
    if (!m || m.reverted) continue
    if (!remedy && m.role === 'assistant' && (m.content || '').trim()) {
      remedy = m.content.trim()
    } else if (!you && m.role === 'user' && (m.content || '').trim()) {
      you = m.content.trim()
    }
    if (you && remedy) break
  }
  return { you, remedy }
}
