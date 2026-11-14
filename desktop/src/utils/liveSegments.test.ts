import { describe, expect, it } from 'vitest'
import { activityLabel, splitLiveText, summarizeSteps } from './liveSegments'

describe('splitLiveText', () => {
  it('treats text before tool calls as narration and the rest as the reply', () => {
    const a = "I'll reach the portal folder through the shell."
    const b = '\n\nCmd is eating the search patterns.'
    const c = '\n\nDone — the addon now loads.'
    const text = a + b + c
    const out = splitLiveText(text, [a.length, a.length + b.length])
    expect(out.narration).toEqual([
      "I'll reach the portal folder through the shell.",
      'Cmd is eating the search patterns.',
    ])
    expect(out.reply).toBe('Done — the addon now loads.')
  })
  it('is all reply with no breaks', () => {
    expect(splitLiveText('Hello there.', [])).toEqual({ narration: [], reply: 'Hello there.' })
  })
  it('has an empty reply while a tool runs after narration', () => {
    expect(splitLiveText('Checking the logs.', [18])).toEqual({
      narration: ['Checking the logs.'],
      reply: '',
    })
  })
  it('ignores unsorted, duplicate, and out-of-range breaks', () => {
    const out = splitLiveText('One.Two.', [4, 99, 4, 0, -3])
    expect(out).toEqual({ narration: ['One.', 'Two.'], reply: '' })
  })
  it('keeps the reply markdown intact (only leading whitespace trimmed)', () => {
    expect(splitLiveText('Plan.\n\n- a\n- b\n', [5]).reply).toBe('- a\n- b\n')
  })
})

describe('activity summary', () => {
  const steps = [
    { status: 'done' as const },
    { status: 'error' as const },
    { status: 'running' as const },
  ]
  it('counts steps by status', () => {
    expect(summarizeSteps(steps)).toEqual({ total: 3, running: 1, done: 1, errors: 1 })
  })
  it('labels working vs worked', () => {
    expect(activityLabel(summarizeSteps(steps), true)).toBe('Working… · 3 steps · 1 error')
    expect(activityLabel(summarizeSteps([{ status: 'done' }]), false)).toBe('Worked · 1 step')
  })
})
