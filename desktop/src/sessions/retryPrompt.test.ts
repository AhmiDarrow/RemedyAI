import { describe, expect, it } from 'vitest'
import {
  promoteQueuedOptions,
  resolveBusySend,
  retrySendOptions,
} from './retryPrompt'

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

describe('resolveBusySend', () => {
  it('keeps a landed steer', () => {
    expect(resolveBusySend({ steered: true })).toBe('steered')
    expect(resolveBusySend({ explicit: 'steer', steered: true })).toBe('steered')
  })

  it('queues after when steer misses or rejects — never interrupts', () => {
    expect(resolveBusySend({ steered: false })).toBe('after')
    expect(resolveBusySend({ explicit: 'steer', steered: false })).toBe('after')
    expect(resolveBusySend({ explicit: undefined, steered: false })).toBe('after')
  })

  it('interrupts only when the owner asked', () => {
    expect(resolveBusySend({ explicit: 'interrupt', steered: false })).toBe(
      'interrupt',
    )
    expect(resolveBusySend({ explicit: 'interrupt', steered: true })).toBe(
      'interrupt',
    )
  })

  it('attachments never steer; they queue after unless interrupt', () => {
    expect(
      resolveBusySend({ hasAttachments: true, steered: true }),
    ).toBe('after')
    expect(
      resolveBusySend({
        hasAttachments: true,
        explicit: 'interrupt',
        steered: false,
      }),
    ).toBe('interrupt')
  })

  it('explicit after stays after even if a steer would have landed', () => {
    expect(resolveBusySend({ explicit: 'after', steered: true })).toBe('after')
  })
})
