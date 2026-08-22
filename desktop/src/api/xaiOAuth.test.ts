import { describe, expect, it } from 'vitest'
import { xaiModelAfterOauth, xaiOauthSessionPhase } from './xaiOAuth'

describe('xaiModelAfterOauth', () => {
  it('keeps an existing grok model', () => {
    expect(xaiModelAfterOauth('grok-3')).toBe('grok-3')
    expect(xaiModelAfterOauth('Grok-4')).toBe('Grok-4')
  })

  it('falls back to the provider default from discovery / catalog, never a hardcoded id', () => {
    expect(xaiModelAfterOauth('claude-opus-5', 'grok-9')).toBe('grok-9')
    expect(xaiModelAfterOauth('', 'grok-9')).toBe('grok-9')
    expect(xaiModelAfterOauth(undefined, 'grok-9')).toBe('grok-9')
  })

  it('is empty when no default is known yet', () => {
    expect(xaiModelAfterOauth('claude-opus-5')).toBe('')
    expect(xaiModelAfterOauth(undefined, null)).toBe('')
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
