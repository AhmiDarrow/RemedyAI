import { describe, expect, it } from 'vitest'
import {
  lifeTaskHeadline,
  parseLifeTaskChoice,
  shouldShowLifeTaskBanner,
} from './LifeTaskBanner'

describe('parseLifeTaskChoice', () => {
  it('maps short Yes / No / Explain answers', () => {
    expect(parseLifeTaskChoice('Yes')).toBe('yes')
    expect(parseLifeTaskChoice('go ahead.')).toBe('yes')
    expect(parseLifeTaskChoice('No')).toBe('no')
    expect(parseLifeTaskChoice('not now')).toBe('no')
    expect(parseLifeTaskChoice('Explain')).toBe('explain')
    expect(parseLifeTaskChoice('why?')).toBe('explain')
  })

  it('does not steal a longer message', () => {
    expect(parseLifeTaskChoice('yes, and also run the tests')).toBe(null)
    expect(parseLifeTaskChoice('explain the plan then buy milk')).toBe(null)
  })
})

describe('lifeTaskHeadline', () => {
  it('prefers the spoken sentence', () => {
    expect(
      lifeTaskHeadline({
        spoken: 'Step 3 of 5 — adding milk.',
        goal: 'buy milk',
        step: 3,
        total: 5,
      }),
    ).toBe('Step 3 of 5 — adding milk.')
  })

  it('falls back to step N of M', () => {
    expect(
      lifeTaskHeadline({
        goal: 'buy milk',
        step: 3,
        total: 5,
        title: 'adding milk',
      }),
    ).toBe('Step 3 of 5 — adding milk.')
  })
})

describe('shouldShowLifeTaskBanner', () => {
  it('hides cancelled and empty cards', () => {
    expect(shouldShowLifeTaskBanner(null)).toBe(false)
    expect(shouldShowLifeTaskBanner({ status: 'cancelled' })).toBe(false)
  })

  it('shows a live drive and a review', () => {
    expect(shouldShowLifeTaskBanner({ status: 'running', step: 1, total: 3 })).toBe(true)
    expect(shouldShowLifeTaskBanner({ status: 'done', spoken: 'Done — milk.' })).toBe(true)
    expect(shouldShowLifeTaskBanner({ status: 'need_you', approval_id: 'a1' })).toBe(true)
  })
})
