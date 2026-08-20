/** Storyline mapper: chat sessions fold into plain-language moments. */

import { describe, expect, it } from 'vitest'
import {
  describeToolCall,
  latestExchange,
  messagesToMoments,
} from './storylineMoments'
import type { ChatMessage } from '../types'

function msg(partial: Partial<ChatMessage>): ChatMessage {
  return {
    id: partial.id || Math.random().toString(36).slice(2),
    role: partial.role || 'user',
    content: partial.content ?? '',
    thinking: null,
    tool_calls: partial.tool_calls || [],
    tool_results: [],
    model: null,
    agent: null,
    tokens: null,
    created_at: partial.created_at || '2026-08-16T09:00:00Z',
    reverted: partial.reverted ?? false,
  }
}

describe('describeToolCall', () => {
  it('turns computer actions into plain language, never raw JSON', () => {
    expect(
      describeToolCall({ name: 'computer_navigate', args: { url: 'https://www.kroger.com/cart' } }),
    ).toBe('Opened kroger.com')
    expect(
      describeToolCall({
        name: 'computer_act',
        args: { url: 'https://www.kroger.com', click: 'Add to Cart' },
      }),
    ).toBe('Went to kroger.com, pressed “Add to Cart”')
    expect(describeToolCall({ name: 'computer_click', args: { text: 'Sign in' } })).toBe(
      'Pressed “Sign in”',
    )
    expect(describeToolCall({ name: 'mail_send', args: {} })).toContain('go-ahead')
    expect(
      describeToolCall({ name: 'host_run', args: { command: 'exit /b 7' } }),
    ).toBe('Ran a command on this PC')
  })

  it('never exposes typed text (secrets ride machine-side)', () => {
    const d = describeToolCall({
      name: 'computer_type',
      args: { text: 'hunter2-secret' },
    })
    expect(d).toBe('Typed into the focused field')
    expect(d).not.toContain('hunter2')
  })

  it('returns null for unknown tools (caller folds them away)', () => {
    expect(describeToolCall({ name: 'weird_tool', args: {} })).toBeNull()
  })
})

describe('messagesToMoments', () => {
  it('maps a life-task exchange into you-said / remedy-did / remedy-said', () => {
    const moments = messagesToMoments([
      msg({ id: 'm1', role: 'user', content: 'Do the groceries, peppers not mushrooms' }),
      msg({
        id: 'm2',
        role: 'assistant',
        content: 'Cart is ready — I stopped before paying.',
        tool_calls: [
          { name: 'computer_navigate', args: { url: 'https://www.kroger.com' } },
          { name: 'computer_snapshot', args: {} }, // quiet observation → folded
          { name: 'computer_act', args: { click: 'Add to Cart' } },
        ],
      }),
    ])
    expect(moments.map((m) => m.kind)).toEqual([
      'you-said',
      'remedy-did',
      'remedy-did',
      'remedy-said',
    ])
    expect(moments[0].text).toContain('groceries')
    expect(moments[1].text).toBe('Opened kroger.com')
    expect(moments[3].text).toContain('stopped before paying')
  })

  it('skips reverted and system messages and dedupes repeated actions', () => {
    const moments = messagesToMoments([
      msg({ role: 'system', content: 'internal' }),
      msg({ role: 'user', content: 'hello', reverted: true }),
      msg({
        role: 'assistant',
        content: '',
        tool_calls: [
          { name: 'computer_click', args: { text: 'Next' } },
          { name: 'computer_click', args: { text: 'Next' } },
        ],
      }),
    ])
    expect(moments).toHaveLength(1)
    expect(moments[0].kind).toBe('remedy-did')
  })
})

describe('latestExchange', () => {
  it('returns the newest user + assistant caption pair', () => {
    const ex = latestExchange([
      msg({ role: 'user', content: 'old question' }),
      msg({ role: 'assistant', content: 'old answer' }),
      msg({ role: 'user', content: 'swap mushrooms for peppers' }),
      msg({ role: 'assistant', content: 'Done — total dropped 80 cents.' }),
    ])
    expect(ex.you).toBe('swap mushrooms for peppers')
    expect(ex.remedy).toContain('80 cents')
  })

  it('handles empty history', () => {
    expect(latestExchange([])).toEqual({ you: null, remedy: null })
  })
})
