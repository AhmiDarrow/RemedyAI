import { describe, expect, it } from 'vitest'
import {
  estimateCostUsd,
  formatCacheUsage,
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

describe('prompt-cache usage', () => {
  it('shows cache reads, and writes when the provider reports both', () => {
    expect(
      formatCacheUsage({
        prompt_tokens: 1000,
        completion_tokens: 10,
        total_tokens: 1010,
        estimated_cost_usd: 0,
        cache_read_tokens: 24000,
      }),
    ).toBe('24k cached')
    expect(
      formatCacheUsage({
        prompt_tokens: 1000,
        completion_tokens: 10,
        total_tokens: 1010,
        estimated_cost_usd: 0,
        cache_read_tokens: 24000,
        cache_write_tokens: 1500,
      }),
    ).toBe('24k cached · 1.5k written')
  })

  it('draws nothing for providers that do not cache', () => {
    expect(
      formatCacheUsage({
        prompt_tokens: 10,
        completion_tokens: 5,
        total_tokens: 15,
        estimated_cost_usd: 0,
      }),
    ).toBe('')
    expect(formatCacheUsage(null)).toBe('')
  })

  it('carries provider cache counts through the live run estimate', () => {
    const live = liveRunEstimate('partial', '', 'claude-opus-5', 'anthropic', {
      prompt_tokens: 900,
      completion_tokens: 2,
      total_tokens: 902,
      estimated_cost_usd: 0,
      cache_read_tokens: 40000,
      cache_write_tokens: 800,
      source: 'provider',
    })
    expect(live.cache_read_tokens).toBe(40000)
    expect(live.cache_write_tokens).toBe(800)
  })
})
