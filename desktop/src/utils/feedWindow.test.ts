import { describe, expect, it } from 'vitest'
import { FEED_WINDOW, feedHiddenCount, feedNextChunk, feedWindowStart } from './feedWindow'

describe('feed window', () => {
  it('keeps the newest 80 rows mounted by default', () => {
    expect(FEED_WINDOW).toBe(80)
    expect(feedWindowStart(250, 0)).toBe(170)
    expect(feedHiddenCount(250, 0)).toBe(170)
  })

  it('reveals earlier rows in chunks of 80 instead of all at once', () => {
    let revealed = 0
    const seen: number[] = []
    while (feedHiddenCount(250, revealed) > 0) {
      const chunk = feedNextChunk(250, revealed)
      seen.push(chunk)
      revealed += chunk
    }
    expect(seen).toEqual([80, 80, 10])
    expect(feedWindowStart(250, revealed)).toBe(0)
  })

  it('never hides anything for short transcripts', () => {
    expect(feedWindowStart(12, 0)).toBe(0)
    expect(feedHiddenCount(80, 0)).toBe(0)
    expect(feedNextChunk(80, 0)).toBe(0)
  })

  it('clamps garbage input', () => {
    expect(feedWindowStart(-5, -3)).toBe(0)
    expect(feedWindowStart(100, 1e9)).toBe(0)
  })
})
