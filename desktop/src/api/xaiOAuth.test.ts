import { describe, expect, it } from 'vitest'
import { xaiModelAfterOauth, xaiOauthSessionPhase } from './xaiOAuth'

describe('xaiModelAfterOauth', () => {
  it('keeps an existing grok model', () => {
    expect(xaiModelAfterOauth('grok-3')).toBe('grok-3')
    expect(xaiModelAfterOauth('Grok-4')).toBe('Grok-4')
  })

  it('falls back when the current model is not grok', () => {
    expect(xaiModelAfterOauth('claude-opus-5')).toBe('grok-4.3')
    expect(xaiModelAfterOauth('')).toBe('grok-4.3')
    expect(xaiModelAfterOauth(undefined)).toBe('grok-4.3')
  })
})

describe('xaiOauthSessionPhase', () => {
  it('does not treat a pre-existing API key as this OAuth finishing', () => {
    expect(
      xaiOauthSessionPhase({
        session: { session_id: 'ABCD', status: 'pending' },
      }),
    ).toBe('pending')
  })

  it('requires the device-code session status', () => {
    expect(
      xaiOauthSessionPhase({
        session: { session_id: 'ABCD', status: 'connected' },
      }),
    ).toBe('connected')
    expect(
      xaiOauthSessionPhase({
        session: { session_id: 'ABCD', status: 'error', error: 'expired' },
      }),
    ).toBe('error')
  })

  it('ignores leftover credentials.connected while this session is pending', () => {
    expect(
      xaiOauthSessionPhase({
        session: { session_id: 'ABCD', status: 'pending' },
      }),
    ).toBe('pending')
  })
})
