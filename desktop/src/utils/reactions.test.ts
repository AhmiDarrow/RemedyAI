import { describe, expect, it } from 'vitest'
import type { ChatMessage } from '../types'
import { REACTIONS, isReaction, nextReaction, reactingSide, setReactions, withReaction } from './reactions'

const msgs = (): ChatMessage[] => [
  { id: 'u1', role: 'user', content: 'hi', created_at: '' } as ChatMessage,
  { id: 'a1', role: 'assistant', content: 'hello', created_at: '' } as ChatMessage,
]

describe('reactions', () => {
  it('offers exactly the four reactions', () => {
    expect(REACTIONS.map((r) => r.emoji)).toEqual(['👍', '👎', '❤️', '💔'])
    expect(isReaction('❤️')).toBe(true)
    expect(isReaction('🎉')).toBe(false)
  })

  it('tapping the same reaction again removes it; another replaces it', () => {
    expect(nextReaction(undefined, '👍')).toBe('👍')
    expect(nextReaction('👍', '👍')).toBe('')
    expect(nextReaction('👍', '💔')).toBe('💔')
  })

  it('the owner reacts to replies and Remedy to the owner', () => {
    expect(reactingSide('assistant')).toBe('user')
    expect(reactingSide('user')).toBe('agent')
  })

  it('keeps one reaction per side and leaves the other side alone', () => {
    let m = withReaction(msgs(), 'a1', 'agent', '👍')
    m = withReaction(m, 'a1', 'user', '❤️')
    m = withReaction(m, 'a1', 'user', '👎')
    expect(m[1]!.reactions).toEqual({ agent: '👍', user: '👎' })
    m = withReaction(m, 'a1', 'user', '')
    expect(m[1]!.reactions).toEqual({ agent: '👍' })
    m = withReaction(m, 'a1', 'agent', '')
    expect(m[1]!.reactions).toBeUndefined()
  })

  it('returns the same array when nothing changes', () => {
    const base = msgs()
    expect(withReaction(base, 'missing', 'user', '👍')).toBe(base)
    expect(withReaction(base, 'a1', 'user', '')).toBe(base)
  })

  it('reconciles with the server copy', () => {
    const m = setReactions(msgs(), 'u1', { agent: '👍' })
    expect(m[0]!.reactions).toEqual({ agent: '👍' })
    expect(setReactions(m, 'u1', null)[0]!.reactions).toBeUndefined()
  })
})
