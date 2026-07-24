import { describe, expect, it } from 'vitest'
import { formatApiErrorBody } from './client'

describe('formatApiErrorBody', () => {
  it('prefers string detail', () => {
    expect(formatApiErrorBody({ detail: 'boom' })).toBe('boom')
  })

  it('flattens FastAPI validation arrays', () => {
    const msg = formatApiErrorBody({
      detail: [{ loc: ['body', 'llm_provider'], msg: 'field required' }],
    })
    expect(msg).toContain('llm_provider')
    expect(msg).toContain('field required')
  })

  it('falls back for empty body', () => {
    expect(formatApiErrorBody(null, 'fallback')).toBe('fallback')
  })
})
