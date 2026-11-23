import { describe, expect, it } from 'vitest'
import {
  closeThinkingRound,
  joinThinkingDelta,
  paragraphizeThinking,
  splitThinkingChunks,
  thinkingDisplayChunks,
} from './thinkingText'

describe('joinThinkingDelta', () => {
  it('concatenates same-round tokens without inventing a space', () => {
    expect(joinThinkingDelta('Hel', 'lo')).toBe('Hello')
    expect(joinThinkingDelta('Hello ', 'there.')).toBe('Hello there.')
  })

  it('starts a new paragraph after a closed round, even if the next chunk has no leading newline', () => {
    const closed = closeThinkingRound('First round ends here.')
    expect(joinThinkingDelta(closed, 'Second round begins.')).toBe(
      'First round ends here.\n\nSecond round begins.',
    )
  })

  it('replace-round keeps earlier closed rounds and writes a new last chunk', () => {
    const closed = closeThinkingRound('Round one. It has two sentences.')
    expect(joinThinkingDelta(closed, 'Round two starts.', 'replace-round')).toBe(
      'Round one. It has two sentences.\n\nRound two starts.',
    )
    expect(joinThinkingDelta(closed + 'Round two starts.', 'Round two snapshot.', 'replace-round')).toBe(
      'Round one. It has two sentences.\n\nRound two snapshot.',
    )
  })

  it('replace-round without a prior close replaces the only open chunk (legacy snapshot)', () => {
    expect(joinThinkingDelta('think-a', 'round-two', 'replace-round')).toBe('round-two')
  })
})

describe('thinkingDisplayChunks', () => {
  it('preserves author line breaks and splits rounds on blank lines', () => {
    expect(splitThinkingChunks('Alpha line\nstays together.\n\nBeta round.')).toEqual([
      'Alpha line\nstays together.',
      'Beta round.',
    ])
  })

  it('breaks a long run-on paragraph into readable sentence groups', () => {
    const wall =
      'The addon folder is under the portal install. The search pattern escaping in cmd is the problem, so use PowerShell for the recursive search. Then compare the manifest entry with what the loader requires. Rename confirmed in history. Patch manifest and loader together. After that, run the self-check and only then clean up the scratch copies I made earlier.'
    const chunks = paragraphizeThinking(wall)
    expect(chunks.length).toBeGreaterThan(1)
    expect(chunks.join(' ')).toContain('portal install')
    expect(chunks.join(' ')).toContain('scratch copies')
    expect(chunks.some((c) => c.includes('\n'))).toBe(false)
  })

  it('leaves a short thought as a single chunk', () => {
    expect(thinkingDisplayChunks('Need the folder path first.')).toEqual([
      'Need the folder path first.',
    ])
  })
})
