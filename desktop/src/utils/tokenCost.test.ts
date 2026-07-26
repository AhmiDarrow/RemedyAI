import { describe, expect, it } from 'vitest'
import {
  estimateCostUsd,
  formatTokens,
  liveRunEstimate,
  pricePerMtok,
} from './tokenCost'

describe('tokenCost pricing', () => {
  it('matches grok-4.5 before generic grok-4', () => {
    const [pin, pout] = pricePerMtok('grok-4.5', 'xai')
    expect(pin).toBe(2.0)
    expect(pout).toBe(6.0)
  })

  it('live estimate grows with partial text', () => {
    const u = liveRunEstimate('hello world '.repeat(50), 'thinking…', 'grok-4.5', 'xai')
    expect(u.completion_tokens).toBeGreaterThan(10)
    expect(u.total_tokens).toBeGreaterThan(10)
    expect(u.estimated_cost_usd).toBeGreaterThanOrEqual(0)
  })

  it('formats tokens', () => {
    expect(formatTokens(42)).toBe('42')
    expect(formatTokens(1500)).toMatch(/1\.5k/)
  })

  it('cost is non-zero for priced models', () => {
    expect(estimateCostUsd(1000, 1000, 'grok-4.5', 'xai')).toBeGreaterThan(0)
  })
})
