import { describe, expect, it } from 'vitest'
import { streamHttpErrorMessage } from './messages'

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
