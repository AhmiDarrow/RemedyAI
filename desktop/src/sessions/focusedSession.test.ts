import { afterEach, describe, expect, it } from 'vitest'
import {
  getFocusedSessionId,
  isFocusedSession,
  sessionSelectHeaders,
  setFocusedSessionId,
  shouldRetryStreamAfter409,
} from './focusedSession'

describe('focusedSession', () => {
  afterEach(() => {
    setFocusedSessionId(null)
  })

  it('tracks the focused tab', () => {
    setFocusedSessionId('s-a')
    expect(getFocusedSessionId()).toBe('s-a')
    expect(isFocusedSession('s-a')).toBe(true)
    expect(isFocusedSession('s-b')).toBe(false)
    expect(sessionSelectHeaders('s-a')).toEqual({ 'X-Remedy-Session-Select': '1' })
    expect(sessionSelectHeaders('s-b')).toEqual({})
  })

  it('does not 409-retry a Stopped or unfocused session', () => {
    setFocusedSessionId('s-a')
    expect(shouldRetryStreamAfter409('s-a', { aborted: false })).toBe(true)
    expect(shouldRetryStreamAfter409('s-a', { aborted: true })).toBe(false)
    expect(shouldRetryStreamAfter409('s-stopped', { aborted: false })).toBe(false)
  })
})
