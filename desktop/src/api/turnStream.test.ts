import { describe, expect, it } from 'vitest'
import { frameSeq, readGapFrame, readUsageFrame, SeqGate } from './turnStream'

describe('SeqGate', () => {
  it('applies each seq once so a replay overlapping the live tail is harmless', () => {
    const gate = new SeqGate()
    expect([1, 2, 3].map((n) => gate.admit(n))).toEqual([true, true, true])
    // Second attach replays 1..3 and continues at 4.
    expect([1, 2, 3].map((n) => gate.admit(n))).toEqual([false, false, false])
    expect(gate.admit(4)).toBe(true)
    expect(gate.floor).toBe(4)
  })

  it('starts from an explicit resume point', () => {
    const gate = new SeqGate(10)
    expect(gate.admit(9)).toBe(false)
    expect(gate.admit(10)).toBe(false)
    expect(gate.admit(11)).toBe(true)
  })

  it('parks the floor at the hole so a re-sync resumes exactly there', () => {
    const gate = new SeqGate()
    gate.admit(1)
    gate.admit(2)
    // 3 is dropped; 4..6 still arrive.
    gate.admit(4)
    gate.admit(5)
    gate.admit(6)
    expect(gate.floor).toBe(2)
    // Re-attach from the floor: 3 lands, 4..6 are already applied.
    expect(gate.admit(3)).toBe(true)
    expect([4, 5, 6].map((n) => gate.admit(n))).toEqual([false, false, false])
    expect(gate.floor).toBe(6)
  })

  it('never filters frames that carry no seq', () => {
    const gate = new SeqGate(50)
    expect(gate.admit(0)).toBe(true)
    expect(gate.admit(0)).toBe(true)
  })
})

describe('frameSeq', () => {
  it('reads a positive seq and treats anything else as absent', () => {
    expect(frameSeq({ seq: 7 })).toBe(7)
    expect(frameSeq({ seq: '9' })).toBe(9)
    expect(frameSeq({})).toBe(0)
    expect(frameSeq({ seq: 0 })).toBe(0)
    expect(frameSeq({ seq: 'x' })).toBe(0)
  })
})

describe('readGapFrame', () => {
  it('carries the lost range and the resume point', () => {
    expect(readGapFrame({ from: 12, to: 18, resume_after: 11 })).toEqual({
      from: 12,
      to: 18,
      resumeAfter: 11,
    })
  })

  it('derives resume_after from the range when the server omits it', () => {
    expect(readGapFrame({ from: 5, to: 5 }).resumeAfter).toBe(4)
  })
})

describe('readUsageFrame', () => {
  it('reads the provider payload the Go runtime spreads, cache counts included', () => {
    expect(
      readUsageFrame({
        prompt_tokens: 1200,
        completion_tokens: 300,
        cache_read_tokens: 9000,
        cache_write_tokens: 400,
      }),
    ).toMatchObject({
      prompt_tokens: 1200,
      completion_tokens: 300,
      total_tokens: 1500,
      cache_read_tokens: 9000,
      cache_write_tokens: 400,
    })
  })

  it('reads the design shorthand in/out/cache_read/cache_write', () => {
    expect(readUsageFrame({ in: 80, out: 20, cache_read: 4096, cache_write: 64 })).toMatchObject({
      prompt_tokens: 80,
      completion_tokens: 20,
      total_tokens: 100,
      cache_read_tokens: 4096,
      cache_write_tokens: 64,
    })
  })

  it('leaves cache counts undefined when the provider does not cache', () => {
    const usage = readUsageFrame({ prompt_tokens: 10, completion_tokens: 5 })
    expect(usage.cache_read_tokens).toBeUndefined()
    expect(usage.cache_write_tokens).toBeUndefined()
  })
})
