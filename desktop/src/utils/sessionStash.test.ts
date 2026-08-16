import { describe, expect, it } from 'vitest'
import {
  NONE_SESSION_KEY,
  sessionStashKey,
  swapSessionStash,
} from './sessionStash'

describe('sessionStashKey', () => {
  it('uses _none when there is no chat yet', () => {
    expect(sessionStashKey(null)).toBe(NONE_SESSION_KEY)
    expect(sessionStashKey('')).toBe(NONE_SESSION_KEY)
    expect(sessionStashKey('  ')).toBe(NONE_SESSION_KEY)
  })

  it('trims a real session id', () => {
    expect(sessionStashKey(' abc ')).toBe('abc')
  })
})

describe('swapSessionStash', () => {
  it('stashes the old tab and restores the next', () => {
    const stash = new Map<string, string>()
    const first = swapSessionStash(stash, 'a', 'b', 'draft-a', '')
    expect(first).toEqual({ key: 'b', value: '', carried: false })
    expect(stash.get('a')).toBe('draft-a')

    const back = swapSessionStash(stash, 'b', 'a', 'draft-b', '')
    expect(back).toEqual({ key: 'a', value: 'draft-a', carried: false })
    expect(stash.get('b')).toBe('draft-b')
  })

  it('carries empty-shell draft only onto the session just created', () => {
    const stash = new Map<string, string>()
    const stolen = swapSessionStash(
      stash,
      NONE_SESSION_KEY,
      'existing',
      'hello',
      '',
    )
    expect(stolen).toEqual({ key: 'existing', value: '', carried: false })
    expect(stash.get(NONE_SESSION_KEY)).toBe('hello')

    const created = swapSessionStash(
      stash,
      NONE_SESSION_KEY,
      'sess-1',
      'hello',
      '',
      'sess-1',
    )
    expect(created).toEqual({ key: 'sess-1', value: 'hello', carried: true })
    expect(stash.has(NONE_SESSION_KEY)).toBe(false)
    expect(stash.get('sess-1')).toBe('hello')
  })

  it('carries the live draft, not a stale _none park', () => {
    const stash = new Map<string, string>()
    stash.set(NONE_SESSION_KEY, 'old parked')
    const created = swapSessionStash(
      stash,
      NONE_SESSION_KEY,
      'sess-1',
      'edited after park',
      '',
      'sess-1',
    )
    expect(created).toEqual({
      key: 'sess-1',
      value: 'edited after park',
      carried: true,
    })
    expect(stash.get('sess-1')).toBe('edited after park')
    expect(stash.has(NONE_SESSION_KEY)).toBe(false)
  })

  it('is a no-op when the key does not change', () => {
    const stash = new Map<string, string>()
    const out = swapSessionStash(stash, 'a', 'a', 'keep', '')
    expect(out).toEqual({ key: 'a', value: 'keep', carried: false })
    expect(stash.size).toBe(0)
  })
})
