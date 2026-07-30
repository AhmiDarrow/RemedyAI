import { describe, expect, it } from 'vitest'
import { promoteQueuedOptions, retrySendOptions } from './retryPrompt'

describe('retrySendOptions', () => {
  it('preserves per-session provider on Stop & retry', () => {
    const opts = retrySendOptions({
      text: 'continue',
      model: 'grok-4',
      provider: 'xai',
      sid: 'sess-1',
    })
    expect(opts).toEqual({ mode: 'after', provider: 'xai' })
    expect('provider' in opts).toBe(true)
  })

  it('forwards undefined provider explicitly (no silent drop of key)', () => {
    const opts = retrySendOptions({ text: 'hi', model: 'demo' })
    expect(opts.mode).toBe('after')
    expect(opts.provider).toBeUndefined()
  })
})

describe('promoteQueuedOptions', () => {
  it('keeps queued provider when interrupting', () => {
    expect(promoteQueuedOptions({ provider: 'ollama' })).toEqual({
      mode: 'interrupt',
      provider: 'ollama',
    })
  })
})
