import { describe, expect, it } from 'vitest'
import { preopenSignInWindow, sendSignInWindowTo, xaiModelAfterOauth, xaiOauthSessionPhase } from './xaiOAuth'

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

describe('web sign-in window (popup blocker)', () => {
  const fakeWindow = () => {
    const w = {
      closed: false,
      opener: {} as unknown,
      location: { href: 'about:blank' },
      document: { title: '', body: { style: { cssText: '' }, textContent: '' } },
      close() {
        this.closed = true
      },
    }
    return w
  }

  it('opens during the click on the web, and not at all on desktop', () => {
    const w = fakeWindow()
    const calls: string[] = []
    const open = (u: string) => {
      calls.push(u)
      return w as unknown as Window
    }
    expect(preopenSignInWindow(open, () => false)).toBe(w)
    expect(calls).toEqual(['about:blank'])
    expect(preopenSignInWindow(open, () => true)).toBeNull()
    expect(calls).toHaveLength(1)
  })

  it('a blocked or throwing open yields null, never an error', () => {
    expect(preopenSignInWindow(() => null, () => false)).toBeNull()
    expect(
      preopenSignInWindow(() => {
        throw new Error('blocked')
      }, () => false),
    ).toBeNull()
  })

  it('sends the pre-opened window to the verify page and drops the opener', () => {
    const w = fakeWindow()
    expect(sendSignInWindowTo(w as unknown as Window, 'https://accounts.x.ai/device?code=AB')).toBe(true)
    expect(w.location.href).toBe('https://accounts.x.ai/device?code=AB')
    expect(w.opener).toBeNull()
  })

  it('falls back when the window is gone, closed, or there is no url', () => {
    const w = fakeWindow()
    w.closed = true
    expect(sendSignInWindowTo(w as unknown as Window, 'https://x')).toBe(false)
    expect(sendSignInWindowTo(null, 'https://x')).toBe(false)
    expect(sendSignInWindowTo(undefined, 'https://x')).toBe(false)
    expect(sendSignInWindowTo(fakeWindow() as unknown as Window, '')).toBe(false)
  })
})
