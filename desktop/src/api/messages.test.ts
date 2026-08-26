import { describe, expect, it } from 'vitest'
import { streamHttpErrorMessage, streamTransportErrorMessage } from './messages'

describe('streamHttpErrorMessage', () => {
  it('prefers string detail', () => {
    expect(streamHttpErrorMessage({ detail: 'rate limited' }, 429, 'Too Many Requests')).toBe(
      'rate limited',
    )
  })

  it('flattens FastAPI validation arrays (not [object Object])', () => {
    const msg = streamHttpErrorMessage(
      {
        detail: [
          { loc: ['body', 'message'], msg: 'field required', type: 'missing' },
          { loc: ['body', 'provider'], msg: 'value is not a valid enumeration member' },
        ],
      },
      422,
      'Unprocessable Entity',
    )
    expect(msg).toContain('message')
    expect(msg).toContain('field required')
    expect(msg).toContain('provider')
    expect(msg).not.toContain('[object Object]')
  })

  it('falls back to status text / HTTP code', () => {
    expect(streamHttpErrorMessage({}, 503, 'Service Unavailable')).toBe('Service Unavailable')
    expect(streamHttpErrorMessage(null, 500, '')).toBe('HTTP 500')
  })
})

describe('streamTransportErrorMessage', () => {
  it('rewrites WebView2 network error as a local drop', () => {
    const msg = streamTransportErrorMessage(new Error('network error'))
    expect(msg.toLowerCase()).toContain('local server')
    expect(msg.toLowerCase()).toContain('continue')
    expect(msg.toLowerCase()).not.toBe('network error')
  })

  it('rewrites wrapped / non-Error network drops too', () => {
    for (const err of [
      new Error('TypeError: network error'),
      new Error('a network error occurred'),
      'network error', // non-Error rejection
      new Error('Load failed'),
      new Error('TypeError: Load failed'),
    ]) {
      expect(streamTransportErrorMessage(err).toLowerCase()).toContain('local server')
    }
  })

  it('keeps a real provider message', () => {
    expect(streamTransportErrorMessage(new Error('rate limited'))).toBe('rate limited')
    // Backend text that merely contains "load failed" is not a transport drop.
    expect(streamTransportErrorMessage(new Error('model load failed'))).toBe('model load failed')
  })
})
