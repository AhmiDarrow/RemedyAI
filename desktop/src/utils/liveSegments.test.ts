import { describe, expect, it } from 'vitest'
import { narrationAtBreak, splitLiveText, splitSavedReply, summarizeSteps } from './liveSegments'

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
})

describe('narrationAtBreak', () => {
  const text = 'Reaching the folder.\n\nFound it.'
  it('returns the text cut by a newly added break', () => {
    expect(narrationAtBreak(text, [], [20])).toBe('Reaching the folder.')
    expect(narrationAtBreak(text, [20], [20, text.length])).toBe('Found it.')
  })
  it('is empty when the call added no break', () => {
    expect(narrationAtBreak(text, [20], [20])).toBe('')
  })
})

describe('splitSavedReply', () => {
  const content =
    "I'll reach the folder.\n\nFound the manifest.\n\nThe addon loads again.\n\n**What changed:** loader."
  it('removes recorded narration so the bubble holds only the reply', () => {
    const r = splitSavedReply(content, [
      { narration: "I'll reach the folder." },
      {},
      { narration: 'Found the manifest.' },
    ])
    expect(r.narration).toEqual(["I'll reach the folder.", 'Found the manifest.'])
    expect(r.reply).toBe('The addon loads again.\n\n**What changed:** loader.')
  })
  it('leaves older saves without narration untouched', () => {
    expect(splitSavedReply(content, [{}, {}])).toEqual({ narration: [], reply: content })
  })
  it('skips narration that is not in the content instead of guessing', () => {
    const r = splitSavedReply(content, [{ narration: 'Not there.' }])
    expect(r.reply).toBe(content)
  })
  it('does not remove a repeated phrase from the reply after the narration', () => {
    const c = 'Checking.\n\nDone. Checking again later is not needed.'
    const r = splitSavedReply(c, [{ narration: 'Checking.' }])
    expect(r.reply).toBe('Done. Checking again later is not needed.')
  })
})
