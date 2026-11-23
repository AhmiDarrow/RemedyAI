import { describe, expect, it } from 'vitest'
import { assistantBeats, narrationAtBreak, splitLiveText, splitSavedReply, splitSealedLive, summarizeSteps } from './liveSegments'

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

describe('splitSealedLive', () => {
  it('exposes completed mid-turn speech separately from the live reply', () => {
    const a = "I'll reach the folder."
    const b = 'Found it.'
    const reply = 'The addon loads.'
    const text = `${a}\n\n${b}\n\n${reply}`
    const out = splitSealedLive(text, [a.length, a.length + 2 + b.length])
    expect(out.narration).toEqual([a, b])
    expect(out.reply).toBe(reply)
    expect(out.sealed).toBe(`${a}\n\n${b}`)
  })

  it('has no sealed text before the first tool-call break', () => {
    expect(splitSealedLive('Hello there.', [])).toEqual({
      narration: [],
      reply: 'Hello there.',
      sealed: '',
    })
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

describe('assistantBeats', () => {
  it('turns mid-turn narration into separate beats and keeps the final reply distinct', () => {
    const segs = splitLiveText(
      "I'll reach the folder.\n\nFound the manifest.\n\nThe addon loads now.",
      [22, 44],
    )
    const beats = assistantBeats(segs)
    expect(beats.map((b) => [b.kind, b.text])).toEqual([
      ['mid', "I'll reach the folder."],
      ['mid', 'Found the manifest.'],
      ['final', 'The addon loads now.'],
    ])
  })

  it('a plain reply with no breaks is a single solo beat', () => {
    expect(assistantBeats(splitLiveText('Hi there.', []))).toEqual([
      { text: 'Hi there.', kind: 'solo' },
    ])
  })
})
