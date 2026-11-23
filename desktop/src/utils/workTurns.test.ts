import { describe, expect, it } from 'vitest'
import type { ChatMessage } from '../types'
import { attachLiveWork, promptExcerpt, turnIdForMessages, workTurnsFromMessages } from './workTurns'

const base = {
  thinking: null as string | null,
  tool_calls: [] as ChatMessage['tool_calls'],
  tool_results: [] as ChatMessage['tool_results'],
  model: null,
  agent: null,
  tokens: null,
  created_at: '2026-11-22T21:00:00Z',
  reverted: false,
}

const user = (id: string, content: string): ChatMessage => ({
  ...base,
  id,
  role: 'user',
  content,
})

const assistant = (
  id: string,
  content: string,
  extra: Partial<ChatMessage> = {},
): ChatMessage => ({
  ...base,
  id,
  role: 'assistant',
  content,
  ...extra,
})

describe('workTurnsFromMessages', () => {
  it('anchors assistant thinking and tools to the user prompt that started the turn', () => {
    const turns = workTurnsFromMessages([
      user('u1', 'Fix the portal addon please'),
      assistant('a1', "I'll reach the folder.", {
        thinking: 'Need the path first.',
        tool_calls: [{ name: 'bash', args: { command: 'dir' }, id: 'c1' }],
        tool_results: [{ name: 'bash', output: 'ok', id: 'c1' }],
      }),
      user('u2', 'Thanks'),
      assistant('a2', 'Anytime.'),
    ])
    expect(turns).toHaveLength(2)
    expect(turns[0]!.id).toBe('u1')
    expect(turns[0]!.prompt).toContain('portal addon')
    expect(turns[0]!.thinking).toBe('Need the path first.')
    expect(turns[0]!.steps).toHaveLength(1)
    expect(turns[0]!.steps[0]!.name).toBe('bash')
    expect(turns[0]!.assistantIds).toEqual(['a1'])
    expect(turns[1]!.id).toBe('u2')
    expect(turns[1]!.steps).toHaveLength(0)
    expect(turns[1]!.thinking).toBe('')
  })

  it('joins two assistant messages on the same user turn and skips reverted rows', () => {
    const turns = workTurnsFromMessages([
      user('u1', 'Go'),
      { ...assistant('old', 'nope'), reverted: true },
      assistant('a1', 'mid', { thinking: 'Round one.' }),
      assistant('a2', 'final', {
        thinking: 'Round two.',
        tool_calls: [{ name: 'file_read', args: { path: 'a.ts' }, id: 'c2' }],
        tool_results: [{ name: 'file_read', output: 'src', id: 'c2' }],
      }),
    ])
    expect(turns).toHaveLength(1)
    expect(turns[0]!.thinking).toBe('Round one.\n\nRound two.')
    expect(turns[0]!.steps).toHaveLength(1)
    expect(turns[0]!.assistantIds).toEqual(['a1', 'a2'])
  })

  it('keeps an orphan assistant turn when there is no user prompt', () => {
    const turns = workTurnsFromMessages([
      assistant('a1', 'Hi', { thinking: 'Just greeting.' }),
    ])
    expect(turns).toHaveLength(1)
    expect(turns[0]!.userMessageId).toBeNull()
    expect(turns[0]!.thinking).toBe('Just greeting.')
  })
})

describe('attachLiveWork', () => {
  it('folds live thinking, tools, and todos into the last user turn', () => {
    const baseTurns = workTurnsFromMessages([user('u1', 'Fix it'), assistant('a0', 'earlier')])
    const live = attachLiveWork(baseTurns, {
      streaming: true,
      thinking: 'Looking at the loader.',
      steps: [
        {
          id: 's1',
          name: 'bash',
          label: 'Running command',
          status: 'running',
          startedAt: 1,
        },
      ],
      todos: [{ id: 't1', content: 'Patch the loader', status: 'in_progress' }],
      lastUserId: 'u1',
    })
    expect(live).toHaveLength(1)
    expect(live[0]!.live).toBe(true)
    expect(live[0]!.thinking).toBe('Looking at the loader.')
    expect(live[0]!.steps[0]!.status).toBe('running')
    expect(live[0]!.todos?.[0]!.content).toContain('loader')
  })

  it('drops empty historical turns and keeps leftover open todos after the stream', () => {
    const turns = workTurnsFromMessages([
      user('u1', 'Hi'),
      assistant('a1', 'Hello'),
      user('u2', 'Build it'),
    ])
    const next = attachLiveWork(turns, {
      streaming: false,
      lastUserId: 'u2',
      todos: [{ id: 't1', content: 'Still open', status: 'pending' }],
    })
    expect(next.map((t) => t.id)).toEqual(['u2'])
    expect(next[0]!.live).toBeFalsy()
    expect(next[0]!.todos).toHaveLength(1)
  })
})

describe('turn helpers', () => {
  it('maps assistant bubbles back to the user turn and excerpts the prompt', () => {
    const msgs = [user('u1', 'One'), assistant('a1', 'ok'), user('u2', 'Two'), assistant('a2', 'ok')]
    const map = turnIdForMessages(msgs)
    expect(map.get('a1')).toBe('u1')
    expect(map.get('a2')).toBe('u2')
    expect(promptExcerpt('  Fix   the addon  now  ', 20)).toBe('Fix the addon now')
    expect(promptExcerpt('The portal addon stopped loading after the last update. Can you fix it?', 40)).toMatch(/…$/)
  })
})
