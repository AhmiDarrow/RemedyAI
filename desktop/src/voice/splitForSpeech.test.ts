import { describe, expect, it } from 'vitest'
import { splitForSpeech } from './useVoice'

describe('splitForSpeech', () => {
  it('keeps a short reply as one chunk', () => {
    expect(splitForSpeech('Sure. I can do that.')).toEqual(['Sure. I can do that.'])
  })
  it('splits a long reply on sentence ends, first chunk short so it plays soon', () => {
    const text =
      'First thing first, the plan is ready and waiting for you to read. ' +
      'Second, I moved the meeting to Tuesday because the room was double-booked. ' +
      'Third, your receipt from the pharmacy is filed under health. ' +
      'Anything else?'
    const chunks = splitForSpeech(text)
    expect(chunks.length).toBeGreaterThanOrEqual(3)
    expect(chunks[0].length).toBeLessThanOrEqual(120)
    expect(chunks.join(' ')).toBe(text)
  })
  it('never returns an empty list', () => {
    expect(splitForSpeech('no terminal punctuation here')).toEqual(['no terminal punctuation here'])
  })
})
